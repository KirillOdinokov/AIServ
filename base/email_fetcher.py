"""Забор прайс-листов с почты (IMAP) и создание задач прайсов.

Подключается к почтовому ящику (по умолчанию price@enix.ooo на Beget),
забирает непрочитанные письма с вложениями-прайсами (.doc/.docx/.xls/.xlsx/.csv),
сохраняет вложение, создаёт price_list и запускает разбор.

Переменные окружения (.env):
  EMAIL_IMAP_HOST    — IMAP-хост (по умолчанию imap.beget.com)
  EMAIL_IMAP_PORT    — порт (993)
  EMAIL_USER         — логин (price@enix.ooo)
  EMAIL_PASS         — пароль
  EMAIL_BOX          — ящик (INBOX)
  EMAIL_SUPPLIER_MAP — JSON: {"отправитель@домен": "Имя поставщика"}
                       (если пусто — поставщик заводится по имени отправителя)
"""
import email as email_lib
import html as html_lib
import imaplib
import json
import os
import re
import threading
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

import db
import email_price_service
import price_service

IMAP_HOST = os.getenv("EMAIL_IMAP_HOST", "imap.beget.com")
IMAP_PORT = int(os.getenv("EMAIL_IMAP_PORT", "993"))
EMAIL_USER = os.getenv("EMAIL_USER", "price@enix.ooo")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
EMAIL_BOX = os.getenv("EMAIL_BOX", "INBOX")
POLL_SECONDS = int(os.getenv("EMAIL_POLL_SECONDS", "600"))

ALLOWED_EXT = {".doc", ".docx", ".xls", ".xlsx", ".csv"}


def _supplier_map():
    raw = os.getenv("EMAIL_SUPPLIER_MAP", "")
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _dec(s):
    if not s:
        return ""
    try:
        out = []
        for t, enc in decode_header(s):
            out.append(t.decode(enc or "utf-8", "replace") if isinstance(t, bytes) else t)
        return "".join(out)
    except Exception:
        return str(s)


def _addr(msg, key):
    raw = msg.get(key, "")
    s = _dec(raw)
    m = re.search(r"[\w.+-]+@[\w.-]+", s)
    return m.group(0).lower() if m else s.strip().lower()


def _attachments(msg):
    out = []
    for part in msg.walk():
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" not in disp:
            continue
        filename = part.get_filename()
        if not filename:
            continue
        filename = _dec(filename)
        if Path(filename).suffix.lower() not in ALLOWED_EXT:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            out.append((filename, payload))
    return out


def _body_text(msg):
    """Текст письма: text/plain, при его отсутствии — HTML без тегов."""
    plain, html_parts = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            chunk = payload.decode(charset, "replace")
        except LookupError:
            chunk = payload.decode("utf-8", "replace")
        (plain if ctype == "text/plain" else html_parts).append(chunk)
    text = "\n".join(plain).strip()
    if not text and html_parts:
        raw = "\n".join(html_parts)
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>", "\n", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        text = html_lib.unescape(raw)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _resolve_supplier(from_addr, filename, subject):
    """Определить поставщика прайса.

    1) явный маппинг отправителя (EMAIL_SUPPLIER_MAP) — для прямых писем производителя;
    2) имя поставщика встретилось в имени файла/теме письма — для пересланных менеджером
       (@enix.ru) прайсов, где поставщика опознаём по файлу;
    3) иначе None — поставщика выбирают вручную в карточке прайса.
    """
    smap = _supplier_map()
    if from_addr and from_addr in smap:
        name = smap[from_addr]
        row = db.query("SELECT id FROM suppliers WHERE lower(name) = lower(%s) LIMIT 1", (name,))
        if row:
            return row[0]["id"]
        return db.query("INSERT INTO suppliers (name) VALUES (%s) RETURNING id", (name,))[0]["id"]

    haystack = " ".join(x for x in (filename, subject) if x).lower()
    if not haystack:
        return None

    # предпочитаем поставщиков, привязанных к сайту (для матчинга цен), затем по длине имени
    suppliers = db.query(
        "SELECT id, name, "
        "(SELECT count(*) FROM sites WHERE supplier_id = suppliers.id) AS n_sites "
        "FROM suppliers ORDER BY n_sites DESC, char_length(name) DESC"
    )
    # 1) полное имя поставщика встретилось в файле/теме
    for s in suppliers:
        n = (s["name"] or "").strip().lower()
        if len(n) >= 3 and n in haystack:
            return s["id"]
    # 2) значимое слово из имени поставщика (>= 4 букв) встретилось в файле/теме
    for s in suppliers:
        n = (s["name"] or "").strip().lower()
        for tok in re.findall(r"[a-zа-яё]{4,}", n):
            if tok in haystack:
                return s["id"]
    return None


def fetch_once():
    if not EMAIL_PASS:
        return 0
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(EMAIL_USER, EMAIL_PASS)
        M.select(EMAIL_BOX)
        typ, data = M.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        processed = 0
        for num in ids:
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email_lib.message_from_bytes(msg_data[0][1])
            message_id = (msg.get("Message-ID") or "").strip()
            from_addr = _addr(msg, "From")
            subject = _dec(msg.get("Subject", ""))[:500]
            try:
                received = parsedate_to_datetime(msg.get("Date"))
            except Exception:
                received = None
            attachs = _attachments(msg)

            if not attachs:
                text = _body_text(msg)
                if text and email_price_service.looks_like_request(text):
                    if message_id and db.query(
                        "SELECT 1 FROM emails WHERE message_id = %s", (message_id,)
                    ):
                        M.store(num, "+FLAGS", "(\\Seen)")
                        continue
                    email_id = None
                    try:
                        em = db.query(
                            "INSERT INTO emails (message_id, mailbox, from_addr, subject, received_at, "
                            "has_attachments, body_text, kind, status) "
                            "VALUES (%s,%s,%s,%s,%s,false,%s,'price_text','processing') RETURNING id",
                            (message_id or None, EMAIL_USER, from_addr or None,
                             subject or None, received, text[:20000]),
                        )
                        email_id = em[0]["id"]
                    except Exception:
                        email_id = None
                    try:
                        result = email_price_service.process(
                            from_addr, subject, message_id, text
                        )
                        if email_id:
                            if result.get("ignored"):
                                status = "ignored"
                            elif result.get("ok"):
                                status = "processed"
                            else:
                                status = "failed"
                            db.execute(
                                "UPDATE emails SET status=%s, processed_at=now(), notes=%s WHERE id=%s",
                                (status, str(result)[:2000], email_id),
                            )
                    except Exception as e:
                        print(f"[email_fetcher] price text failed: {e}")
                    M.store(num, "+FLAGS", "(\\Seen)")
                    processed += 1
                    continue
                M.store(num, "+FLAGS", "(\\Seen)")
                continue

            # пропустить уже обработанный message_id
            if message_id and db.query("SELECT 1 FROM emails WHERE message_id = %s", (message_id,)):
                M.store(num, "+FLAGS", "(\\Seen)")
                continue

            try:
                em = db.query(
                    "INSERT INTO emails (message_id, mailbox, from_addr, subject, received_at, "
                    "has_attachments, kind, status) VALUES (%s,%s,%s,%s,%s,true,'price','processing') "
                    "RETURNING id",
                    (message_id or None, EMAIL_USER, from_addr or None, subject or None, received),
                )
                email_id = em[0]["id"]
            except Exception:
                email_id = None

            supplier_id = _resolve_supplier(
                from_addr,
                attachs[0][0] if attachs else None,
                subject,
            )
            for filename, data in attachs:
                try:
                    stored, ext = price_service.save_upload(data, filename)
                    pid = price_service.create_price_list(
                        filename, str(stored), ext, supplier_id,
                        price_date=price_service.guess_price_date(filename),
                        note=("Из письма" + ((" " + from_addr) if from_addr else "")),
                    )
                    if email_id:
                        db.execute(
                            "INSERT INTO email_attachments (email_id, filename, stored_path, "
                            "file_type, price_list_id) VALUES (%s,%s,%s,%s,%s)",
                            (email_id, filename, str(stored), ext, pid),
                        )
                    price_service.start_processing(pid)
                except Exception as e:
                    print(f"[email_fetcher] attachment failed {filename!r}: {e}")
            if email_id:
                db.execute("UPDATE emails SET status='processed', processed_at=now() WHERE id=%s", (email_id,))
            M.store(num, "+FLAGS", "(\\Seen)")
            processed += 1
        return processed
    finally:
        try:
            M.logout()
        except Exception:
            pass


def _loop():
    while True:
        try:
            fetch_once()
        except Exception as e:
            print(f"[email_fetcher] {e}")
        time.sleep(POLL_SECONDS)


def start():
    threading.Thread(target=_loop, daemon=True).start()
