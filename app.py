import os
import re
import csv
import io
import json
import time
import hmac
import sqlite3
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, session, make_response, abort, Response
)
from werkzeug.security import check_password_hash, generate_password_hash
from database import get_db, get_category_tree, init_db, DB_PATH
import notify
from translations import localize_combo, en_item
from seed_data import seed_database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================================
# SECURITY CONFIG
# =====================================================================

def _load_secret_key():
    """Session-signing key.
    1) FLASK_SECRET_KEY env variable (recommended on a live server)
    2) otherwise a random key is generated once and saved in `.secret_key`
       next to this file (NEVER upload/commit that file).
    There is no hard-coded key anymore, so nobody can forge an admin cookie.
    """
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        return env_key
    key_path = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(key_path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        with open(key_path, "w", encoding="utf-8") as f:
            f.write(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return key


# Admin login. Only a HASH of the password is stored (never the password itself).
# The very first password is "qw12PrimoraKitr4" - change it in the admin panel ("পাসওয়ার্ড পরিবর্তন").
# After you change it, the new hash is kept in the database and this default is no longer used.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = "qw12PrimoraKitr4"
DEFAULT_ADMIN_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or (
    "scrypt:32768:8:1$NNR8xFqZSUbN2xha$4f533ef09aa12d1ae626fe068aacf4d0fb6e55328eb39695e2a5dbc3df2580679d6594598beb9a7e14a63c49f58ae91f402f5229ec698424ae22521166dab091"
)

app = Flask(__name__)
app.secret_key = _load_secret_key()
app.jinja_env.globals["en_item"] = en_item
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Set COOKIE_SECURE=1 once the site runs on HTTPS
    SESSION_COOKIE_SECURE=(os.environ.get("COOKIE_SECURE") == "1"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=256 * 1024,  # reject huge request bodies
)

# If you run behind Nginx / a proxy, set TRUST_PROXY=1 so the real visitor IP is used
if os.environ.get("TRUST_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

# Simple in-memory brute-force protection for /admin/login
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_MAX_FAILS = 5
_login_fails = {}

# Make sure tables exist and old plain-text admin credentials are purged from the DB
init_db()
seed_database()   # adds the combos / products on the very first start (does nothing afterwards)


def current_admin_hash():
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'admin_password_hash'").fetchone()
        if row and row["value"]:
            return row["value"]
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password_hash', ?)", (DEFAULT_ADMIN_HASH,))
    return DEFAULT_ADMIN_HASH


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(24)
    return session["csrf"]


def csrf_ok():
    return hmac.compare_digest(str(session.get("csrf", "")), str(request.form.get("csrf", "")))


current_admin_hash()  # creates the first hash on the very first start

# Bangladeshi Mobile Number Regex: 013, 014, 015, 016, 017, 018, 019 + 8 digits = 11 digits
PHONE_REGEX = re.compile(r"^01[3-9]\d{8}$")
VALID_STATUSES = ["Pending", "Confirmed", "Processing", "Shipped", "Delivered", "Cancelled"]
VALID_ZONES = ["kushtia_iu", "inside_dhaka", "outside_dhaka"]
VALID_PAYMENTS = ["cod", "bkash", "nagad"]
MAX_QTY_PER_ITEM = 50
MAX_CART_LINES = 30

BD_TZ = timezone(timedelta(hours=6))
DRAFT_ID_RE = re.compile(r"^[a-f0-9]{16,40}$")
DRAFT_EMAIL_MIN_GAP = 20          # seconds between two e-mails for the same draft
DRAFT_EMAIL_HOURLY_CAP = 150      # safety limit so nobody can flood the inbox
DRAFT_RATE_WINDOW = 10 * 60
DRAFT_RATE_MAX = 90               # draft saves per visitor IP per 10 minutes
_draft_hits = {}
_draft_mail_times = []

_BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


@app.template_filter("bn_digits")
def bn_digits(value):
    return str(value).translate(_BN_DIGITS)


def to_bd_time(value):
    """Database times are UTC; show Bangladesh time (UTC+6) to people."""
    try:
        dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(BD_TZ).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value or "")[:16]


app.template_filter("bd_time")(to_bd_time)


@app.template_filter("bn_money")
def bn_money(value):
    try:
        return ("৳" + str(int(round(float(value))))).translate(_BN_DIGITS)
    except (TypeError, ValueError):
        return "৳০"


@app.after_request
def set_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path.startswith("/admin"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Cache-Control"] = "no-store"
    return resp


def get_settings(conn):
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    # secrets (passwords, tokens) and admin data must never reach a public page
    return {row["key"]: row["value"] for row in rows
            if not row["key"].startswith(("secret_", "admin_"))}


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return decorated_function


def _safe_next(target):
    """Only allow redirects to our own paths (prevents open-redirect)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return None


def _login_locked(ip):
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_fails[ip] = fails
    return len(fails) >= LOGIN_MAX_FAILS


def generate_order_number():
    date_str = datetime.now().strftime("%Y%m%d")
    random_suffix = secrets.token_hex(2).upper()
    return f"PK-{date_str}-{random_suffix}"


def get_delivery_fee(zone):
    fees = {
        "kushtia_iu": 0.0,       # 100% Free delivery for Kushtia & Islamic University!
        "inside_dhaka": 60.0,    # Inside Dhaka City
        "outside_dhaka": 120.0   # Outside Dhaka / Nationwide
    }
    return fees.get(zone, 120.0)


def clean_phone(value):
    value = (value or "").replace(" ", "").replace("-", "")
    if value.startswith("+88"):
        value = value[3:]
    elif value.startswith("88"):
        value = value[2:]
    return value


def csv_safe(value):
    """Stops spreadsheet formula injection (=, +, -, @) in the courier CSV."""
    if value is None:
        return ""
    text = str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


# ==================== PUBLIC STOREFRONT & API ====================

@app.route("/")
def index():
    with get_db() as conn:
        combos = conn.execute("SELECT * FROM combos WHERE is_active = 1 ORDER BY price ASC").fetchall()
        products = conn.execute("SELECT * FROM products WHERE is_active = 1 ORDER BY id ASC").fetchall()
        categories = conn.execute("SELECT * FROM categories WHERE is_active = 1 ORDER BY sort_order ASC").fetchall()
        settings = get_settings(conn)

    parsed_combos = []
    for c in combos:
        c_dict = dict(c)
        c_dict["items_breakdown"] = json.loads(c_dict["items_breakdown_json"])
        parsed_combos.append(localize_combo(c_dict))

    category_tree = get_category_tree()

    return render_template(
        "index.html",
        combos=parsed_combos,
        products=[dict(p) for p in products],
        categories=[dict(cat) for cat in categories],
        category_tree=category_tree,
        settings=settings
    )


@app.route("/cart")
def cart_page():
    """Cart + one-shot checkout page. The cart itself lives in the browser (localStorage);
    prices are always re-verified on the server when the order is placed."""
    with get_db() as conn:
        settings = get_settings(conn)
    fees = {zone: get_delivery_fee(zone) for zone in VALID_ZONES}
    return render_template("cart.html", settings=settings, fees=fees)


@app.route("/api/categories")
def get_categories():
    tree = get_category_tree()
    return jsonify({"status": "success", "categories": tree})


@app.route("/api/combos")
def get_combos():
    with get_db() as conn:
        combos = conn.execute("SELECT * FROM combos WHERE is_active = 1").fetchall()
        data = []
        for c in combos:
            item = dict(c)
            item["items_breakdown"] = json.loads(item["items_breakdown_json"])
            data.append(localize_combo(item))
    return jsonify({"status": "success", "combos": data})


@app.route("/api/orders", methods=["POST"])
def place_order():
    data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
    if not isinstance(data, dict):
        data = {}

    name = (data.get("customer_name") or "").strip()[:100]
    phone = clean_phone((data.get("phone") or "").strip())
    alt_phone = clean_phone((data.get("alternative_phone") or "").strip())
    address = (data.get("address") or "").strip()[:300]
    delivery_zone = (data.get("delivery_zone") or "outside_dhaka").strip()
    campus_details = (data.get("campus_details") or "").strip()[:200]
    division = (data.get("division") or "").strip()[:60]
    district = (data.get("district") or "").strip()[:60]
    payment_method = (data.get("payment_method") or "cod")
    payment_method = payment_method if payment_method in VALID_PAYMENTS else "cod"
    trx_id = (data.get("trx_id") or "").strip()[:40]
    notes = (data.get("order_notes") or "").strip()[:500]
    raw_items = data.get("items")
    draft_id = str(data.get("draft_id") or "")[:40]

    # Smart defaults for Kushtia & IU
    if delivery_zone == "kushtia_iu":
        division = division or "খুলনা"
        district = district or "কুষ্টিয়া"
        if not address and campus_details:
            address = campus_details

    # Validation
    if not name or len(name) < 2:
        return jsonify({"status": "error", "message": "অনুগ্রহ করে আপনার পূর্ণ নাম লিখুন।"}), 400

    if not PHONE_REGEX.match(phone):
        return jsonify({"status": "error", "message": "সঠিক ১১ ডিজিটের মোবাইল নম্বর দিন (যেমন: 017XXXXXXXX)।"}), 400

    if alt_phone and not PHONE_REGEX.match(alt_phone):
        return jsonify({"status": "error", "message": "বিকল্প মোবাইল নম্বরটি সঠিক নয়। ১১ ডিজিটের নম্বর দিন অথবা ঘরটি খালি রাখুন।"}), 400

    if not address or len(address) < 3:
        return jsonify({"status": "error", "message": "অনুগ্রহ করে বিস্তারিত ঠিকানা বা হল/ডিপার্টমেন্ট উল্লেখ করুন (সর্বনিম্ন ৩ অক্ষর)।"}), 400

    if payment_method in ["bkash", "nagad"] and not trx_id:
        return jsonify({"status": "error", "message": f"{payment_method.upper()} ট্রানজেকশন আইডি (TrxID) প্রদান করুন।"}), 400

    if delivery_zone not in VALID_ZONES:
        delivery_zone = "outside_dhaka"

    # Parse items
    if isinstance(raw_items, str):
        try:
            raw_items = json.loads(raw_items)
        except Exception:
            raw_items = []

    if not raw_items or not isinstance(raw_items, list):
        return jsonify({"status": "error", "message": "অন্তত একটি প্যাকেজ নির্বাচন করুন।"}), 400

    raw_items = raw_items[:MAX_CART_LINES]

    # Strict server-side verification of pricing (the browser price is never trusted)
    with get_db() as conn:
        cursor = conn.cursor()
        subtotal = 0.0
        verified_items = []

        for it in raw_items:
            if not isinstance(it, dict):
                continue
            i_type = it.get("type", "combo")
            i_id = it.get("id", 0)
            try:
                i_qty = int(it.get("qty", 1))
            except (TypeError, ValueError):
                continue
            if i_qty <= 0:
                continue
            i_qty = min(i_qty, MAX_QTY_PER_ITEM)

            row = None
            if i_type == "combo":
                if str(i_id).isdigit():
                    row = cursor.execute("SELECT id, name, bn_name, price FROM combos WHERE id = ? AND is_active = 1", (int(i_id),)).fetchone()
                if not row:
                    row = cursor.execute("SELECT id, name, bn_name, price FROM combos WHERE slug = ? AND is_active = 1", (str(i_id),)).fetchone()
                if not row:
                    all_c = cursor.execute("SELECT id, name, bn_name, price FROM combos WHERE is_active = 1 ORDER BY id ASC").fetchall()
                    if str(i_id) in ["1", 1] and len(all_c) > 0:
                        row = all_c[0]
                    elif str(i_id) in ["2", 2] and len(all_c) > 1:
                        row = all_c[1]
            else:
                i_type = "product"
                if str(i_id).isdigit():
                    row = cursor.execute("SELECT id, name, bn_name, unit_price AS price FROM products WHERE id = ? AND is_active = 1", (int(i_id),)).fetchone()
                if not row:
                    row = cursor.execute("SELECT id, name, bn_name, unit_price AS price FROM products WHERE slug = ? AND is_active = 1", (str(i_id),)).fetchone()

            if not row:
                continue

            unit_price = float(row["price"])
            line_total = unit_price * i_qty
            subtotal += line_total
            verified_items.append({
                "type": i_type,
                "id": row["id"],
                "name": row["bn_name"] or row["name"],
                "unit_price": unit_price,
                "quantity": i_qty,
                "subtotal": line_total
            })

        if not verified_items:
            return jsonify({"status": "error", "message": "কোনো বৈধ আইটেম পাওয়া যায়নি।"}), 400

        delivery_fee = get_delivery_fee(delivery_zone)
        total_amount = subtotal + delivery_fee

        order_number = None
        order_db_id = None
        for _ in range(6):  # retry in the (rare) case of a duplicate order number
            candidate = generate_order_number()
            try:
                cursor.execute("""
                    INSERT INTO orders (
                        order_number, customer_name, phone, alternative_phone,
                        address, division, district, delivery_zone, campus_details,
                        delivery_fee, subtotal, total_amount, payment_method, trx_id,
                        payment_status, order_notes, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    candidate, name, phone, alt_phone,
                    address, division, district, delivery_zone, campus_details,
                    delivery_fee, subtotal, total_amount, payment_method,
                    trx_id if payment_method in ["bkash", "nagad"] else None,
                    "paid" if payment_method in ["bkash", "nagad"] and trx_id else "unpaid",
                    notes, "Pending"
                ))
                order_number = candidate
                order_db_id = cursor.lastrowid
                break
            except sqlite3.IntegrityError:
                continue

        if order_number is None:
            return jsonify({"status": "error", "message": "অর্ডার সম্পন্ন করা যায়নি, আবার চেষ্টা করুন।"}), 500

        for it in verified_items:
            cursor.execute("""
                INSERT INTO order_items (order_id, item_type, item_id, item_name, unit_price, quantity, subtotal)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (order_db_id, it["type"], it["id"], it["name"], it["unit_price"], it["quantity"], it["subtotal"]))

    _after_order_saved(
        {
            "order_number": order_number, "customer_name": name, "phone": phone,
            "alternative_phone": alt_phone, "address": address, "district": district,
            "campus_details": campus_details, "delivery_zone": delivery_zone,
            "delivery_fee": delivery_fee, "subtotal": subtotal, "total_amount": total_amount,
            "payment_method": payment_method,
            "trx_id": trx_id if payment_method in ["bkash", "nagad"] else "",
            "order_notes": notes,
        },
        [{"name": v["name"], "qty": v["quantity"], "price": v["unit_price"]} for v in verified_items],
        draft_id,
    )

    return jsonify({
        "status": "success",
        "message": "আপনার অর্ডারটি সফলভাবে সম্পন্ন হয়েছে!",
        "order_number": order_number,
        "is_free_delivery": (delivery_fee == 0.0),
        "total_amount": total_amount,
        "redirect_url": url_for("order_confirmation", order_number=order_number)
    }), 201


def _admin_url():
    try:
        return request.host_url.rstrip("/") + url_for("admin_dashboard")
    except RuntimeError:
        return "/admin"


def _after_order_saved(order, items, draft_id):
    """Mark the checkout draft as finished and e-mail the order to the shop owner.
    Anything that goes wrong here must never break the customer's order."""
    try:
        with get_db() as conn:
            if DRAFT_ID_RE.match(draft_id or ""):
                conn.execute(
                    "UPDATE checkout_drafts SET status = 'completed', order_number = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE draft_id = ?",
                    (order["order_number"], draft_id))
            # same phone number = same person, so their older unfinished drafts are no longer 'incomplete'
            conn.execute(
                "UPDATE checkout_drafts SET status = 'completed', order_number = ? "
                "WHERE status = 'open' AND phone = ?", (order["order_number"], order["phone"]))
        if notify.any_configured():
            subject, body = notify.build_order_email(order, items, _admin_url())
            notify.notify_async(subject, body)
    except Exception:
        app.logger.exception("post-order tasks failed (the order itself is saved)")


def _draft_rate_limited(ip):
    now = time.time()
    hits = [t for t in _draft_hits.get(ip, []) if now - t < DRAFT_RATE_WINDOW]
    if len(hits) >= DRAFT_RATE_MAX:
        _draft_hits[ip] = hits
        return True
    hits.append(now)
    _draft_hits[ip] = hits
    return False


def _draft_mail_allowed():
    now = time.time()
    _draft_mail_times[:] = [t for t in _draft_mail_times if now - t < 3600]
    if len(_draft_mail_times) >= DRAFT_EMAIL_HOURLY_CAP:
        return False
    _draft_mail_times.append(now)
    return True


@app.route("/api/checkout-draft", methods=["POST"])
def checkout_draft():
    """Saves whatever the customer has typed so far. An e-mail goes to the shop only when the
    customer leaves the page / stays idle without finishing (the browser marks that as `final`)."""
    if _draft_rate_limited(request.remote_addr or "unknown"):
        return jsonify({"status": "error"}), 429

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"status": "error"}), 400
    draft_id = str(data.get("draft_id") or "")
    if not DRAFT_ID_RE.match(draft_id):
        return jsonify({"status": "error"}), 400

    name = str(data.get("customer_name") or "").strip()[:100]
    phone = clean_phone(str(data.get("phone") or "").strip())[:20]
    alt_phone = clean_phone(str(data.get("alternative_phone") or "").strip())[:20]
    address = str(data.get("address") or "").strip()[:300]
    district = str(data.get("district") or "").strip()[:60]
    notes = str(data.get("order_notes") or "").strip()[:500]
    zone = data.get("delivery_zone") if data.get("delivery_zone") in VALID_ZONES else ""
    payment = data.get("payment_method") if data.get("payment_method") in VALID_PAYMENTS else "cod"
    source = "quick" if data.get("source") == "quick" else "cart"
    final = bool(data.get("final"))

    digits = re.sub(r"\D", "", phone)
    if not (len(name) >= 2 or len(digits) >= 6 or len(address) >= 3):
        return jsonify({"status": "ignored"}), 200  # nothing worth saving yet

    with get_db() as conn:
        items = []
        raw_items = data.get("items")
        if isinstance(raw_items, list):
            for it in raw_items[:MAX_CART_LINES]:
                if not isinstance(it, dict):
                    continue
                try:
                    qty = max(1, min(MAX_QTY_PER_ITEM, int(it.get("qty", 1))))
                except (TypeError, ValueError):
                    continue
                key = str(it.get("id", ""))
                row = None
                if key.isdigit():
                    row = conn.execute("SELECT name, bn_name, price FROM combos WHERE id = ? AND is_active = 1", (int(key),)).fetchone()
                if not row:
                    row = conn.execute("SELECT name, bn_name, price FROM combos WHERE slug = ? AND is_active = 1", (key,)).fetchone()
                if row:
                    items.append({"name": row["bn_name"] or row["name"], "qty": qty, "price": float(row["price"])})

        content = json.dumps([name, phone, alt_phone, address, district, zone, payment, notes, items], ensure_ascii=False, sort_keys=True)
        content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()

        row = conn.execute("SELECT * FROM checkout_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
        if row and row["status"] == "completed":
            return jsonify({"status": "ok", "completed": True})

        if row is None:
            conn.execute("""
                INSERT INTO checkout_drafts (draft_id, source, customer_name, phone, alternative_phone, address,
                    district, delivery_zone, payment_method, order_notes, items_json, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (draft_id, source, name, phone, alt_phone, address, district, zone, payment, notes,
                  json.dumps(items, ensure_ascii=False), content_hash))
        else:
            conn.execute("""
                UPDATE checkout_drafts SET source = ?, customer_name = ?, phone = ?, alternative_phone = ?,
                    address = ?, district = ?, delivery_zone = ?, payment_method = ?, order_notes = ?,
                    items_json = ?, content_hash = ?, updated_at = CURRENT_TIMESTAMP
                WHERE draft_id = ?
            """, (source, name, phone, alt_phone, address, district, zone, payment, notes,
                  json.dumps(items, ensure_ascii=False), content_hash, draft_id))

        now = time.time()
        send_now = False
        if final and notify.any_configured():
            changed = row is None or row["last_emailed_hash"] != content_hash
            last = row["last_emailed_at"] if row is not None else None
            if changed and (last is None or now - last >= DRAFT_EMAIL_MIN_GAP) and _draft_mail_allowed():
                send_now = True
                conn.execute("UPDATE checkout_drafts SET last_emailed_at = ?, last_emailed_hash = ? WHERE draft_id = ?",
                             (now, content_hash, draft_id))

        # tidy up very old drafts now and then
        conn.execute("DELETE FROM checkout_drafts WHERE updated_at < datetime('now', '-30 days')")

    if send_now:
        try:
            subject, body = notify.build_draft_email({
                "customer_name": name, "phone": phone, "alternative_phone": alt_phone, "address": address,
                "district": district, "delivery_zone": zone, "payment_method": payment, "order_notes": notes,
            }, items, _admin_url())
            notify.notify_async(subject, body)
        except Exception:
            app.logger.exception("draft notification failed")

    return jsonify({"status": "ok"})


@app.route("/order/<order_number>")
def order_confirmation(order_number):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()
        if not order:
            abort(404)
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order["id"],)).fetchall()
        settings = get_settings(conn)

    return render_template("order_success.html", order=order, items=items, settings=settings)


@app.route("/order/<order_number>/invoice")
def order_invoice(order_number):
    with get_db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()
        if not order:
            abort(404)
        items = conn.execute("SELECT * FROM order_items WHERE order_id = ?", (order["id"],)).fetchall()
        settings = get_settings(conn)

    return render_template("invoice.html", order=order, items=items, settings=settings)


# ==================== PUBLIC TRACKING ====================

@app.route("/track")
def track_page():
    with get_db() as conn:
        settings = get_settings(conn)
    return render_template("track.html", settings=settings)


@app.route("/api/track")
def track_api():
    query = (request.args.get("query") or "").strip()
    if not query:
        return jsonify({"status": "error", "message": "অর্ডার আইডি অথবা ফোন নম্বর প্রদান করুন।"}), 400

    clean_query = query.replace(" ", "").replace("-", "")
    with get_db() as conn:
        if PHONE_REGEX.match(clean_query):
            orders = conn.execute("SELECT * FROM orders WHERE phone = ? ORDER BY id DESC", (clean_query,)).fetchall()
        else:
            orders = conn.execute("SELECT * FROM orders WHERE order_number = ? ORDER BY id DESC", (query.upper(),)).fetchall()

        if not orders:
            return jsonify({"status": "error", "message": "এই তথ্য অনুযায়ী কোনো অর্ডার পাওয়া যায়নি।"}), 404

        output = []
        for o in orders:
            items = conn.execute("SELECT item_name, quantity, subtotal FROM order_items WHERE order_id = ?", (o["id"],)).fetchall()
            output.append({
                "order_number": o["order_number"],
                "customer_name": o["customer_name"],
                "masked_phone": o["phone"][:3] + "****" + o["phone"][-4:],
                "delivery_zone": o["delivery_zone"],
                "campus_details": o["campus_details"],
                "delivery_fee": o["delivery_fee"],
                "total_amount": o["total_amount"],
                "payment_method": o["payment_method"].upper(),
                "status": o["status"],
                "created_at": to_bd_time(o["created_at"]),
                "items": [dict(i, item_name_en=en_item(i["item_name"])) for i in items]
            })

    return jsonify({"status": "success", "orders": output})


# ==================== ADMIN DASHBOARD ====================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        now = time.time()

        # Check if the IP is locked out for 24 hours
        if ip in _login_fails:
            lock_data = _login_fails[ip]
            if isinstance(lock_data, dict) and lock_data.get("locked_until"):
                if now < lock_data["locked_until"]:
                    remaining_hours = int((lock_data["locked_until"] - now) / 3600) + 1
                    return render_template(
                        "admin/login.html",
                        error=f"Too many failed login attempts. Account locked for 24 hours. Please try again in about {remaining_hours} hours."
                    ), 429
                else:
                    _login_fails.pop(ip, None)

        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        user_ok = hmac.compare_digest(username.encode("utf-8"), ADMIN_USERNAME.encode("utf-8"))
        pass_ok = check_password_hash(current_admin_hash(), password)

        if user_ok and pass_ok:
            _login_fails.pop(ip, None)
            session.clear()
            session["is_admin"] = True
            session["admin_user"] = username
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")) or url_for("admin_dashboard"))

        # Manage failed login attempts
        if ip not in _login_fails or not isinstance(_login_fails[ip], dict):
            _login_fails[ip] = {"count": 0, "locked_until": None}
        
        _login_fails[ip]["count"] += 1
        current_count = _login_fails[ip]["count"]
        remaining_tries = 5 - current_count

        if current_count >= 5:
            # Lock for 24 hours (86400 seconds) on 5th failure
            _login_fails[ip]["locked_until"] = now + 86400
            error = "Too many failed attempts. Your account has been locked for 24 hours."
        elif current_count == 2:
            # Warning message after 2 failed attempts
            error = f"Last try! {remaining_tries} tries left"
        else:
            error = "Invalid username or password!"

    return render_template("admin/login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    status_filter = request.args.get("status", "")
    zone_filter = request.args.get("zone", "")
    search = request.args.get("search", "").strip()

    with get_db() as conn:
        # KPI calculations
        total_revenue = conn.execute("SELECT COALESCE(SUM(total_amount), 0) FROM orders WHERE status != 'Cancelled'").fetchone()[0]
        total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        pending_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'Pending'").fetchone()[0]
        confirmed_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'Confirmed'").fetchone()[0]
        shipped_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'Shipped'").fetchone()[0]
        delivered_count = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'Delivered'").fetchone()[0]
        kushtia_count = conn.execute("SELECT COUNT(*) FROM orders WHERE delivery_zone = 'kushtia_iu'").fetchone()[0]

        query = "SELECT * FROM orders WHERE 1=1"
        params = []
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        if zone_filter:
            query += " AND delivery_zone = ?"
            params.append(zone_filter)
        if search:
            query += " AND (order_number LIKE ? OR phone LIKE ? OR customer_name LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        query += " ORDER BY id DESC LIMIT 150"
        order_rows = conn.execute(query, params).fetchall()

        # Attach ordered items to each order (carts can have several items now)
        items_map = {}
        order_ids = [o["id"] for o in order_rows]
        if order_ids:
            placeholders = ",".join("?" * len(order_ids))
            item_rows = conn.execute(
                f"SELECT order_id, item_name, quantity FROM order_items WHERE order_id IN ({placeholders}) ORDER BY id",
                order_ids
            ).fetchall()
            for r in item_rows:
                items_map.setdefault(r["order_id"], []).append(
                    {"item_name": r["item_name"], "quantity": r["quantity"]}
                )

    orders = [dict(o, line_items=items_map.get(o["id"], [])) for o in order_rows]

    with get_db() as conn:
        draft_rows = conn.execute(
            "SELECT * FROM checkout_drafts WHERE status = 'open' ORDER BY updated_at DESC LIMIT 100").fetchall()
    drafts = []
    for d in draft_rows:
        dd = dict(d)
        try:
            dd["items"] = json.loads(dd.get("items_json") or "[]")
        except ValueError:
            dd["items"] = []
        dd["wa_ok"] = bool(PHONE_REGEX.match(dd.get("phone") or ""))
        drafts.append(dd)

    stats = {
        "total_revenue": total_revenue,
        "total_orders": total_orders,
        "pending": pending_count,
        "confirmed": confirmed_count,
        "shipped": shipped_count,
        "delivered": delivered_count,
        "kushtia_orders": kushtia_count,
        "incomplete": len(drafts)
    }

    return render_template(
        "admin/dashboard.html",
        orders=orders,
        drafts=drafts,
        notify_warning=_notify_warning(),
        using_default_password=check_password_hash(current_admin_hash(), DEFAULT_ADMIN_PASSWORD),
        stats=stats,
        valid_statuses=VALID_STATUSES,
        filters={"status": status_filter, "zone": zone_filter, "search": search}
    )


@app.route("/admin/api/orders/<int:order_id>/status", methods=["POST"])
@admin_required
def update_status(order_id):
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in VALID_STATUSES:
        return jsonify({"status": "error", "message": "অবৈধ স্ট্যাটাস"}), 400

    with get_db() as conn:
        conn.execute("UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new_status, order_id))
    return jsonify({"status": "success", "message": f"অর্ডার #{order_id} এর স্ট্যাটাস {new_status} এ আপডেট করা হয়েছে!"})


@app.route("/admin/api/drafts/<int:draft_pk>/delete", methods=["POST"])
@admin_required
def delete_draft(draft_pk):
    with get_db() as conn:
        conn.execute("DELETE FROM checkout_drafts WHERE id = ?", (draft_pk,))
    return jsonify({"status": "success"})


def _notify_warning():
    mail, tg = notify.mail_configured(), notify.telegram_configured()
    if mail and tg:
        return ""
    if not mail and not tg:
        return "Gmail ও Telegram কোনোটাই এখনো চালু হয়নি — নতুন অর্ডার শুধু এই ড্যাশবোর্ডে দেখা যাবে।"
    return "Telegram নোটিফিকেশন এখনো চালু হয়নি।" if mail else "Gmail নোটিফিকেশন এখনো চালু হয়নি।"


@app.route("/admin/api/test-notify", methods=["POST"])
@admin_required
def admin_test_notify():
    result = notify.notify_owner(
        "✅ PRIMORA KIT — টেস্ট মেসেজ",
        "✅ টেস্ট মেসেজ — নোটিফিকেশন ঠিকমতো কাজ করছে!\nএখন থেকে নতুন অর্ডার ও অসম্পূর্ণ অর্ডারের তথ্য এখানে আসবে।")
    lines = []
    for key, label in (("email", "Gmail"), ("telegram", "Telegram")):
        val = result[key]
        if val is True:
            lines.append("✅ %s: পাঠানো হয়েছে" % label)
        elif val is None:
            lines.append("➖ %s: চালু করা নেই" % label)
        else:
            lines.append("❌ %s: %s" % (label, val))
    ok = result["email"] is True or result["telegram"] is True
    return jsonify({"status": "success" if ok else "error", "message": "\n".join(lines)}), 200


@app.route("/admin/api/telegram-chat", methods=["POST"])
@admin_required
def admin_find_telegram_chat():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip() or notify.cfg()["tg_token"]
    if not token:
        return jsonify({"status": "error", "message": "আগে Bot Token দিন (অথবা সেভ করুন)।"}), 400
    try:
        chats = notify.find_telegram_chats(token)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)[:300]}), 400
    if not chats:
        return jsonify({"status": "error", "message": "কোনো চ্যাট পাওয়া যায়নি। Telegram-এ আপনার বটটি খুলে Start চাপুন ও একটি মেসেজ লিখুন, তারপর আবার চেষ্টা করুন।"}), 404
    return jsonify({"status": "success", "chats": chats})


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    msg = None
    if request.method == "POST":
        f = request.form
        if not csrf_ok():
            msg = ("err", "সেশন মেয়াদ শেষ। পেজ রিলোড করে আবার চেষ্টা করুন।")
        else:
            mail_user = (f.get("mail_user") or "").strip()
            mail_to = (f.get("mail_to") or "").strip()
            chat = (f.get("telegram_chat_id") or "").strip()
            new_pass = (f.get("mail_app_password") or "").replace(" ", "")
            new_token = (f.get("telegram_bot_token") or "").strip()
            errors = []
            email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
            if mail_user and not email_re.match(mail_user):
                errors.append("Gmail ঠিকানা সঠিক নয়।")
            if mail_to and not email_re.match(mail_to):
                errors.append("প্রাপক ইমেইল সঠিক নয়।")
            if chat and not re.match(r"^(-?\d{4,}|@[A-Za-z0-9_]{4,})$", chat):
                errors.append("Telegram Chat ID শুধু সংখ্যা হবে (যেমন 7976148479)।")
            if new_pass and not re.match(r"^[A-Za-z0-9]{12,32}$", new_pass):
                errors.append("Gmail App Password সঠিক নয় (১৬ অক্ষর, স্পেস ছাড়া)।")
            if new_token and not notify.TELEGRAM_TOKEN_RE.match(new_token):
                errors.append("Telegram Bot Token-এর ফরম্যাট ঠিক নয়।")
            if errors:
                msg = ("err", " ".join(errors))
            else:
                with get_db() as conn:
                    set_setting(conn, "mail_user", mail_user)
                    set_setting(conn, "mail_to", mail_to)
                    set_setting(conn, "telegram_chat_id", chat)
                    if new_pass:
                        set_setting(conn, "secret_mail_app_password", new_pass)
                    if new_token:
                        set_setting(conn, "secret_telegram_bot_token", new_token)
                    if f.get("clear_mail_password"):
                        set_setting(conn, "secret_mail_app_password", "")
                    if f.get("clear_telegram_token"):
                        set_setting(conn, "secret_telegram_bot_token", "")
                msg = ("ok", "সেভ হয়েছে। এবার ড্যাশবোর্ডের \"🔔 টেস্ট নোটিফিকেশন\" চেপে যাচাই করুন।")
    c = notify.cfg()
    return render_template(
        "admin/settings.html", msg=msg, csrf=csrf_token(),
        mail_user=c["mail_user"], mail_to=c["mail_to"], chat=c["tg_chat"],
        has_pass=bool(c["mail_pass"]), has_token=bool(c["tg_token"]))



@app.route("/admin/password", methods=["GET", "POST"])
@admin_required
def admin_password():
    msg = None
    if request.method == "POST":
        f = request.form
        cur, new, new2 = f.get("current") or "", f.get("new") or "", f.get("new2") or ""
        ip = request.remote_addr or "unknown"
        key = "pw:" + ip
        if not csrf_ok():
            msg = ("err", "সেশন মেয়াদ শেষ। পেজ রিলোড করে আবার চেষ্টা করুন।")
        elif _login_locked(key):
            msg = ("err", "অনেকবার ভুল চেষ্টা। ১০ মিনিট পর আবার চেষ্টা করুন।")
        elif not check_password_hash(current_admin_hash(), cur):
            _login_fails.setdefault(key, []).append(time.time())
            msg = ("err", "বর্তমান পাসওয়ার্ড ভুল।")
        elif len(new) < 10:
            msg = ("err", "নতুন পাসওয়ার্ড কমপক্ষে ১০ অক্ষরের হতে হবে।")
        elif new != new2:
            msg = ("err", "নতুন পাসওয়ার্ড দুটি মিলছে না।")
        elif new == cur:
            msg = ("err", "নতুন পাসওয়ার্ড আগেরটির চেয়ে আলাদা হতে হবে।")
        else:
            with get_db() as conn:
                set_setting(conn, "admin_password_hash", generate_password_hash(new))
            session["csrf"] = secrets.token_hex(24)
            msg = ("ok", "✅ পাসওয়ার্ড পরিবর্তন হয়েছে। এখন থেকে নতুন পাসওয়ার্ড দিয়ে লগইন করবেন।")
    return render_template("admin/password.html", msg=msg, csrf=csrf_token())


@app.route("/admin/backup")
@admin_required
def admin_backup():
    """Downloads a safe, consistent copy of the whole database (orders, customers, everything)."""
    import tempfile
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(tmp_path, "rb") as f:
            payload = f.read()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
    resp = Response(payload, mimetype="application/octet-stream")
    resp.headers["Content-Disposition"] = "attachment; filename=primora_backup_%s.db" % datetime.now(BD_TZ).strftime("%Y%m%d_%H%M")
    return resp


@app.route("/admin/export/csv")
@admin_required
def export_csv():
    with get_db() as conn:
        orders = conn.execute("""
            SELECT order_number, customer_name, phone, address, campus_details, delivery_zone,
                   delivery_fee, total_amount, payment_method, trx_id, status, created_at,
                   (SELECT group_concat(item_name || ' x' || quantity, ' | ')
                      FROM order_items WHERE order_id = orders.id) AS items
            FROM orders ORDER BY id DESC
        """).fetchall()

    si = io.StringIO()
    si.write("\ufeff")  # UTF-8 BOM so Excel shows Bengali text correctly
    writer = csv.writer(si)
    writer.writerow([
        "Order Number", "Customer Name", "Phone", "Full Address",
        "Campus/Local Point", "Delivery Zone", "Delivery Fee (BDT)",
        "Total Amount (BDT)", "Payment Method", "TrxID", "Status", "Date", "Items"
    ])
    for o in orders:
        writer.writerow([
            csv_safe(o["order_number"]), csv_safe(o["customer_name"]), csv_safe(o["phone"]),
            csv_safe(o["address"]), csv_safe(o["campus_details"]), csv_safe(o["delivery_zone"]),
            o["delivery_fee"], o["total_amount"], csv_safe(o["payment_method"]),
            csv_safe(o["trx_id"]), csv_safe(o["status"]), csv_safe(o["created_at"]),
            csv_safe(o["items"])
        ])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=primora_kit_orders_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    output.headers["Content-type"] = "text/csv; charset=utf-8"
    return output


if __name__ == "__main__":
    import sys
    try:  # Windows console: make sure Bengali text can be printed
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # debug is OFF by default. For local development only:  set FLASK_DEBUG=1
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    site = "http://127.0.0.1:%d" % port
    print("\n" + "=" * 62)
    print("  PRIMORA KIT চালু হচ্ছে ...")
    print("  ওয়েবসাইট  : " + site)
    print("  অ্যাডমিন    : " + site + "/admin/login")
    print("  বন্ধ করতে   : এই উইন্ডোতে Ctrl + C চাপুন")
    print("=" * 62 + "\n")
    if host in ("127.0.0.1", "localhost") and os.environ.get("NO_BROWSER") != "1" and os.environ.get("FLASK_DEBUG") != "1":
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(site)).start()
    app.run(debug=(os.environ.get("FLASK_DEBUG") == "1"), host=host, port=port)
