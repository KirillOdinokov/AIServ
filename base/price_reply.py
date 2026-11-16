"""Автоответ отправителю прайса: что обновлено и на каких сайтах.

Письмо отправляется один раз на вложение (email_attachments.notified_at),
в той же ветке (In-Reply-To/References по Message-ID исходного письма).
Если прайс обработан не автоматически, в ответе будет статус разбора
и список сайтов поставщика, ожидающих подтверждения.
"""
import os
import traceback

import db
import mailer

DRY_RUN = os.getenv("EMAIL_REPLY_DRY_RUN", "") not in ("", "0", "false", "no")


def _reply_target(price_list_id):
    rows = db.query(
        "SELECT a.id AS attachment_id, a.notified_at, e.from_addr, e.subject, e.message_id "
        "FROM email_attachments a JOIN emails e ON e.id = a.email_id "
        "WHERE a.price_list_id = %s ORDER BY a.id LIMIT 1",
        (price_list_id,),
    )
    return rows[0] if rows else None


def _mark_notified(attachment_id):
    db.execute("UPDATE email_attachments SET notified_at = now() WHERE id = %s", (attachment_id,))


def _supplier_sites(supplier_id):
    if not supplier_id:
        return []
    return db.query(
        "SELECT id, name, domain FROM sites WHERE supplier_id = %s ORDER BY id",
        (supplier_id,),
    )


def _site_label(site):
    name = site.get("name") or site.get("domain") or ""
    domain = site.get("domain") or ""
    return "%s (%s)" % (name, domain) if domain and domain != name else name


def build_body(filename, result, supplier_sites):
    lines = ["Здравствуйте!", ""]
    lines.append("Прайс-лист «%s» получен сервисом «База»." % filename)

    if not result.get("ok"):
        lines += [
            "",
            "Обработать прайс автоматически не удалось: %s."
            % (result.get("error") or "неизвестная ошибка"),
            "Мы проверим его вручную и свяжемся с вами.",
        ]
    elif result.get("auto_applied"):
        sites = result.get("sites") or []
        total = int(result.get("updated") or 0)
        if sites and total > 0:
            lines += ["", "Цены обновлены:"]
            for site in sites:
                if int(site.get("updated") or 0) <= 0:
                    continue
                lines.append("  • %s — %d поз." % (_site_label(site), int(site.get("updated") or 0)))
            lines += ["", "Всего обновлено позиций: %d." % total]
        elif sites:
            lines += ["", "Прайс обработан: все позиции совпадают с текущими ценами — изменений нет."]
        else:
            lines += ["", "Прайс обработан, но к поставщику не привязан ни один сайт — цены не применены."]
    else:
        lines += [
            "",
            "Прайс разобран: строк — %d, сопоставлено с товарами — %d."
            % (int(result.get("rows") or 0), int(result.get("matched") or 0)),
        ]
        if supplier_sites:
            lines.append("Сайт(ы) поставщика: %s." % ", ".join(_site_label(s) for s in supplier_sites))
        lines += [
            "",
            "Позиции ожидают подтверждения в «Базе»; после подтверждения новые цены будут применены.",
        ]

    lines += [
        "",
        "—",
        "Сервис «База», AI ENIX",
        "Письмо сформировано автоматически, отвечать на него не нужно.",
    ]
    return "\n".join(lines)


def reply_for_price_list(price_list_id, result):
    try:
        target = _reply_target(price_list_id)
        if not target:
            return False
        if target.get("notified_at"):
            return False
        from_addr = (target.get("from_addr") or "").strip()
        if not from_addr or from_addr.lower() == mailer.EMAIL_USER.lower():
            return False

        rec = db.query("SELECT filename, supplier_id FROM price_lists WHERE id = %s", (price_list_id,))
        if not rec:
            return False
        filename = rec[0].get("filename") or "прайс-лист"
        supplier_sites = _supplier_sites(rec[0].get("supplier_id"))

        subject = "Re: %s" % (target.get("subject") or "прайс-лист")
        body = build_body(filename, result, supplier_sites)
        ok = mailer.send(
            from_addr, subject, body,
            in_reply_to=target.get("message_id"),
            references=target.get("message_id"),
            dry_run=DRY_RUN,
        )
        if ok:
            _mark_notified(target["attachment_id"])
        return ok
    except Exception:
        traceback.print_exc()
        return False
