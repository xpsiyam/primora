"""Notifications for the shop owner: Gmail (SMTP) + Telegram (bot). Both are optional.

Everything is configured from the admin panel (Admin -> "নোটিফিকেশন সেটিংস") and saved in the database.
Environment variables (MAIL_APP_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID ...) work too and are used
when the panel has no value. Without any setting nothing is sent and the shop keeps working normally.
"""
import os
import json
import ssl
import smtplib
import threading
import logging
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from database import get_db

log = logging.getLogger("primora.notify")
BD_TZ = timezone(timedelta(hours=6))

ZONE_LABELS = {
    "kushtia_iu": "কুষ্টিয়া শহর ও ইবি (ফ্রি ডেলিভারি)",
    "inside_dhaka": "ঢাকার ভেতরে",
    "outside_dhaka": "ঢাকার বাইরে (সারা বাংলাদেশ)",
}

TELEGRAM_TOKEN_RE = __import__("re").compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")


# --------------------------------------------------------------------------------------
# settings (database first, environment variable second)
# --------------------------------------------------------------------------------------
def _db_setting(key):
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return (row["value"] if row else "") or ""
    except Exception:
        return ""


def cfg():
    return {
        "mail_user": _db_setting("mail_user") or os.environ.get("MAIL_USER", "primorakit@gmail.com"),
        "mail_to": _db_setting("mail_to") or os.environ.get("MAIL_TO", "primorakit@gmail.com"),
        "mail_pass": (_db_setting("secret_mail_app_password") or os.environ.get("MAIL_APP_PASSWORD", "")).replace(" ", ""),
        "mail_host": os.environ.get("MAIL_HOST", "smtp.gmail.com"),
        "mail_port": int(os.environ.get("MAIL_PORT", "465")),
        "tg_token": _db_setting("secret_telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "tg_chat": _db_setting("telegram_chat_id") or os.environ.get("TELEGRAM_CHAT_ID", ""),
    }


def mail_configured():
    c = cfg()
    return bool(c["mail_pass"] and c["mail_user"] and c["mail_to"])


def telegram_configured():
    c = cfg()
    return bool(c["tg_token"] and c["tg_chat"])


def any_configured():
    return mail_configured() or telegram_configured()


# --------------------------------------------------------------------------------------
# Gmail
# --------------------------------------------------------------------------------------
def _one_line(text):
    return " ".join(str(text or "").split())


def send_mail(subject, body):
    """Send one plain-text e-mail. Raises on any problem."""
    c = cfg()
    if not (c["mail_pass"] and c["mail_user"] and c["mail_to"]):
        raise RuntimeError("Gmail App Password সেট করা নেই।")
    msg = EmailMessage()
    msg["Subject"] = _one_line(subject)[:200]
    msg["From"] = "PRIMORA KIT <%s>" % c["mail_user"]
    msg["To"] = c["mail_to"]
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if c["mail_port"] == 587:
        with smtplib.SMTP(c["mail_host"], c["mail_port"], timeout=20) as smtp:
            smtp.starttls(context=ctx)
            smtp.login(c["mail_user"], c["mail_pass"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP_SSL(c["mail_host"], c["mail_port"], timeout=20, context=ctx) as smtp:
            smtp.login(c["mail_user"], c["mail_pass"])
            smtp.send_message(msg)


# --------------------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------------------
def _telegram_call(token, method, params):
    if not TELEGRAM_TOKEN_RE.match(token or ""):
        raise RuntimeError("Telegram Bot Token-এর ফরম্যাট ঠিক নয়।")
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # Telegram answers 400/401/403 with a JSON explanation
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            raise RuntimeError("Telegram সমস্যা: HTTP %s" % exc.code)
    except Exception as exc:
        raise RuntimeError("Telegram সার্ভারে সংযোগ হয়নি: %s" % exc)
    if not payload.get("ok"):
        raise RuntimeError("Telegram: %s" % payload.get("description", "অজানা সমস্যা"))
    return payload


def send_telegram(text):
    c = cfg()
    if not (c["tg_token"] and c["tg_chat"]):
        raise RuntimeError("Telegram Bot Token / Chat ID সেট করা নেই।")
    _telegram_call(c["tg_token"], "sendMessage", {
        "chat_id": c["tg_chat"],
        "text": str(text)[:4000],
        "disable_web_page_preview": "true",
    })


def find_telegram_chats(token):
    """Chats that recently wrote to the bot, so the owner can copy the Chat ID."""
    payload = _telegram_call(token, "getUpdates", {"limit": 50})
    found = {}
    for upd in payload.get("result", []):
        m = upd.get("message") or upd.get("channel_post") or upd.get("my_chat_member") or {}
        chat = m.get("chat") or {}
        if "id" not in chat:
            continue
        name = (" ".join(x for x in [chat.get("first_name"), chat.get("last_name")] if x)
                or chat.get("title") or chat.get("username") or "")
        found[str(chat["id"])] = {"id": str(chat["id"]), "name": name, "type": chat.get("type", "")}
    return list(found.values())


# --------------------------------------------------------------------------------------
# Send to the owner through every channel that is set up
# --------------------------------------------------------------------------------------
def notify_owner(subject, body):
    """Never raises. Returns {"email": None|True|"error text", "telegram": None|True|"error text"}
    (None = that channel is not set up)."""
    result = {"email": None, "telegram": None}
    if mail_configured():
        try:
            send_mail(subject, body)
            result["email"] = True
        except Exception as exc:
            result["email"] = str(exc)[:300]
            log.warning("Gmail notification failed: %s", exc)
    if telegram_configured():
        try:
            send_telegram(body)
            result["telegram"] = True
        except Exception as exc:
            result["telegram"] = str(exc)[:300]
            log.warning("Telegram notification failed: %s", exc)
    return result


def notify_async(subject, body):
    """Fire-and-forget so a slow mail/Telegram server never slows down the customer's checkout."""
    if os.environ.get("MAIL_SYNC") == "1":  # used by the automatic tests
        notify_owner(subject, body)
    else:
        threading.Thread(target=notify_owner, args=(subject, body), daemon=True).start()


# --------------------------------------------------------------------------------------
# Message texts
# --------------------------------------------------------------------------------------
def now_bd():
    return datetime.now(BD_TZ).strftime("%Y-%m-%d %H:%M")


def _items_lines(items):
    lines = []
    for it in items or []:
        qty = int(it.get("qty") or it.get("quantity") or 1)
        price = it.get("price") if it.get("price") is not None else it.get("unit_price")
        name = it.get("name") or it.get("item_name") or "?"
        if price is not None:
            lines.append("  • %s ×%s = ৳%d" % (name, qty, round(float(price) * qty)))
        else:
            lines.append("  • %s ×%s" % (name, qty))
    return "\n".join(lines) if lines else "  (কিছু নেই)"


def build_order_email(o, items, admin_url):
    """o: dict of order fields, items: list of {name, qty, price}."""
    pay = (o.get("payment_method") or "cod").upper()
    if o.get("trx_id"):
        pay += "  (TrxID: %s)" % o["trx_id"]
    body = "\n".join([
        "✅ নতুন অর্ডার এসেছে!",
        "",
        "অর্ডার নং : %s" % o["order_number"],
        "সময়       : %s (বাংলাদেশ সময়)" % now_bd(),
        "",
        "—— গ্রাহক ——",
        "নাম        : %s" % o["customer_name"],
        "মোবাইল    : %s" % o["phone"],
        "বিকল্প নম্বর: %s" % (o.get("alternative_phone") or "—"),
        "এলাকা      : %s" % ZONE_LABELS.get(o.get("delivery_zone"), o.get("delivery_zone")),
        "ঠিকানা     : %s%s" % (o["address"], (", " + o["district"]) if o.get("district") else ""),
        "ক্যাম্পাস   : %s" % (o.get("campus_details") or "—"),
        "",
        "—— পণ্য ——",
        _items_lines(items),
        "",
        "সাবটোটাল  : ৳%d" % round(o["subtotal"]),
        "ডেলিভারি   : ৳%d" % round(o["delivery_fee"]),
        "সর্বমোট    : ৳%d" % round(o["total_amount"]),
        "পেমেন্ট    : %s" % pay,
        "নোট        : %s" % (o.get("order_notes") or "—"),
        "",
        "অ্যাডমিন প্যানেল: %s" % admin_url,
    ])
    subject = "🛒 নতুন অর্ডার %s — ৳%d — %s" % (o["order_number"], round(o["total_amount"]), o["customer_name"])
    return subject, body


def build_draft_email(d, items, admin_url):
    """d: dict of whatever the customer typed so far."""
    phone = d.get("phone") or ""
    wa = ""
    if len(phone) == 11 and phone.startswith("01"):
        wa = "https://wa.me/88%s" % phone
    body = "\n".join([
        "⚠️ অসম্পূর্ণ অর্ডার — গ্রাহক এখনো অর্ডার কনফার্ম করেননি।",
        "গ্রাহক এ পর্যন্ত যতটুকু লিখেছেন:",
        "",
        "নাম        : %s" % (d.get("customer_name") or "—"),
        "মোবাইল    : %s" % (phone or "—"),
        "বিকল্প নম্বর: %s" % (d.get("alternative_phone") or "—"),
        "এলাকা      : %s" % (ZONE_LABELS.get(d.get("delivery_zone")) or "—"),
        "ঠিকানা     : %s%s" % (d.get("address") or "—", (", " + d["district"]) if d.get("district") else ""),
        "পেমেন্ট    : %s" % (d.get("payment_method") or "—").upper(),
        "নোট        : %s" % (d.get("order_notes") or "—"),
        "",
        "—— কার্টে যা আছে ——",
        _items_lines(items),
        "",
        "সময়: %s (বাংলাদেশ সময়)" % now_bd(),
        ("WhatsApp এ যোগাযোগ: %s" % wa) if wa else "",
        "অ্যাডমিন প্যানেল: %s" % admin_url,
    ])
    who = d.get("customer_name") or "নাম নেই"
    subject = "⚠️ অসম্পূর্ণ অর্ডার: %s %s" % (who, phone)
    return subject, body
