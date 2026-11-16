"""Отправка писем через SMTP (тот же ящик, что и приём прайсов).

Переменные окружения (.env):
  EMAIL_SMTP_HOST — SMTP-хост (по умолчанию smtp.beget.com)
  EMAIL_SMTP_PORT — порт SSL (465)
  EMAIL_USER      — логин/адрес отправителя (price@enix.ooo)
  EMAIL_PASS      — пароль
  EMAIL_FROM_NAME — отображаемое имя отправителя
"""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.beget.com")
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER", "price@enix.ooo")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
FROM_NAME = os.getenv("EMAIL_FROM_NAME", "AI ENIX Base")


def send(to_addr, subject, body, in_reply_to=None, references=None, dry_run=False):
    """Отправить письмо. Возвращает True при успехе; ошибки не пробрасывает."""
    if not to_addr:
        return False
    if dry_run:
        print("[mailer] dry-run -> %s | %s\n%s" % (to_addr, subject, body))
        return True
    if not EMAIL_PASS:
        print("[mailer] EMAIL_PASS пуст — отправка пропущена")
        return False
    msg = EmailMessage()
    msg["From"] = "%s <%s>" % (FROM_NAME, EMAIL_USER)
    msg["To"] = to_addr
    msg["Subject"] = subject[:500]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="enix.ooo")
    msg["Auto-Submitted"] = "auto-replied"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.set_content(body, charset="utf-8")
    try:
        with smtplib.SMTP_SSL(
            SMTP_HOST, SMTP_PORT, timeout=25,
            context=ssl.create_default_context(),
        ) as smtp:
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)
    except Exception as exc:
        print("[mailer] send to %s failed: %s" % (to_addr, exc))
        return False
    return True
