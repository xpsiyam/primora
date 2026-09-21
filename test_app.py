import os
import tempfile
import unittest

# Tests use their OWN temporary database and secret key, so your real primora.db is never touched.
_tmp_dir = tempfile.mkdtemp(prefix="primora_test_")
os.environ["PRIMORA_DB_PATH"] = os.path.join(_tmp_dir, "test_primora.db")
os.environ["FLASK_SECRET_KEY"] = "test-secret-key-for-unit-tests-only-0123456789abcdef"
os.environ["MAIL_APP_PASSWORD"] = "dummy-app-password"   # makes e-mail "configured"; sending itself is faked below
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:AAEdummydummydummydummydummydummydum"  # makes Telegram "configured" (chat id comes from the DB default)
os.environ["MAIL_SYNC"] = "1"                           # send inside the request so tests can check it

import notify  # noqa: E402
import app as appmod  # noqa: E402  (must come after the env variables above)
from app import app  # noqa: E402
from database import get_db  # noqa: E402
from seed_data import seed_database  # noqa: E402

ADMIN_PASSWORD = "qw12PrimoraKitr4"


class PrimoraKitTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        appmod._login_fails.clear()
        appmod._draft_hits.clear()
        appmod._draft_mail_times.clear()
        seed_database()
        # every test starts with the default admin password again
        with get_db() as conn:
            conn.execute("UPDATE settings SET value = ? WHERE key = 'admin_password_hash'", (appmod.DEFAULT_ADMIN_HASH,))
        # never send real e-mails from tests: record them instead
        self.sent = []
        self.tg = []
        self._real_send = notify.send_mail
        self._real_tg = notify.send_telegram
        self._real_find = notify.find_telegram_chats
        notify.send_mail = lambda subject, body: self.sent.append((subject, body))
        notify.send_telegram = lambda text: self.tg.append(text)

    def tearDown(self):
        notify.send_mail = self._real_send
        notify.send_telegram = self._real_tg
        notify.find_telegram_chats = self._real_find

    # ---------------- storefront ----------------
    def test_homepage_loads(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn("PRIMORA KIT", html)
        self.assertIn("কুষ্টিয়া শহর ও ইসলামী বিশ্ববিদ্যালয়", html)
        self.assertIn("ফ্রি ডেলিভারি", html)
        self.assertIn("Extra Combo", html)

    def test_order_buttons_add_to_cart_instead_of_redirecting(self):
        html = self.client.get("/").data.decode("utf-8")
        self.assertIn("addToCart(", html)
        self.assertIn('id="cartToast"', html)
        self.assertNotIn("/cart?combo=", html)  # old redirecting links are gone
        self.assertIn('class="cart-count-badge js-cart-badge empty"', html)

    def test_cart_page_loads(self):
        res = self.client.get("/cart")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")
        self.assertIn('id="checkoutForm"', html)
        self.assertIn('id="cartLines"', html)

    def test_founder_intro_only_in_about_us(self):
        html = self.client.get("/").data.decode("utf-8")
        self.assertNotIn("founder-trust-section", html)
        self.assertEqual(html.count('class="about-founder-name"'), 1)
        # nothing about the founder in the footer any more
        footer = html[html.index("<footer"):]
        self.assertNotIn("Siyam Islam", footer)
        self.assertNotIn("CEO", footer)
        # and the intro is not on other pages
        track_html = self.client.get("/track").data.decode("utf-8")
        self.assertNotIn("Siyam Islam", track_html)

    def test_admin_link_not_public(self):
        for path in ("/", "/track", "/cart"):
            html = self.client.get(path).data.decode("utf-8")
            self.assertNotIn("/admin/login", html)

    # ---------------- orders ----------------
    def test_kushtia_iu_free_delivery_order(self):
        payload = {
            "customer_name": "রাকিবুল হাসান",
            "phone": "01712345678",
            "address": "সাদ্দাম হোসেন হল, ২০৪ নং রুম",
            "campus_details": "সাদ্দাম হোসেন হল, ২০৪ নং রুম",
            "delivery_zone": "kushtia_iu",
            "payment_method": "cod",
            "items": [{"type": "combo", "id": "extra-combo", "qty": 1}]
        }
        res = self.client.post("/api/orders", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(data["is_free_delivery"])
        self.assertEqual(data["total_amount"], 130.0)  # 130 + 0 = 130
        order_number = data["order_number"]

        track_res = self.client.get(f"/api/track?query={order_number}")
        self.assertEqual(track_res.status_code, 200)
        tdata = track_res.get_json()
        self.assertEqual(len(tdata["orders"]), 1)
        self.assertEqual(tdata["orders"][0]["delivery_fee"], 0.0)
        self.assertEqual(tdata["orders"][0]["campus_details"], "সাদ্দাম হোসেন হল, ২০৪ নং রুম")

        track_phone_res = self.client.get("/api/track?query=01712345678")
        self.assertEqual(track_phone_res.status_code, 200)

        invoice_res = self.client.get(f"/order/{order_number}/invoice")
        self.assertEqual(invoice_res.status_code, 200)
        invoice_html = invoice_res.data.decode("utf-8")
        self.assertIn(order_number, invoice_html)
        self.assertIn("ফ্রি ডেলিভারি", invoice_html)

    def test_inside_dhaka_delivery_order(self):
        payload = {
            "customer_name": "আরিফুল ইসলাম",
            "phone": "01912345678",
            "address": "মিরপুর ১০, ঢাকা",
            "delivery_zone": "inside_dhaka",
            "payment_method": "cod",
            "items": [{"type": "combo", "id": "exam-kit-combo", "qty": 1}]
        }
        res = self.client.post("/api/orders", json=payload)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["total_amount"], 160.0)  # 100 + 60

    def test_cart_checkout_with_multiple_items_in_one_order(self):
        """Cart with 2x Exam Kit (100) + 1x Extra (130) + outside Dhaka (120) = 450 in ONE order."""
        with get_db() as conn:
            ids = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM combos").fetchall()}
        payload = {
            "customer_name": "কার্ট টেস্ট",
            "phone": "01812345678",
            "address": "রাজশাহী সদর",
            "district": "রাজশাহী",
            "delivery_zone": "outside_dhaka",
            "payment_method": "bkash",
            "trx_id": "9A7B5C3D2E",
            "items": [
                {"type": "combo", "id": str(ids["exam-kit-combo"]), "qty": 2},
                {"type": "combo", "id": str(ids["extra-combo"]), "qty": 1},
            ]
        }
        res = self.client.post("/api/orders", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertEqual(data["total_amount"], 450.0)

        tdata = self.client.get(f"/api/track?query={data['order_number']}").get_json()
        self.assertEqual(len(tdata["orders"][0]["items"]), 2)

    def test_prices_cannot_be_tampered_from_browser(self):
        payload = {
            "customer_name": "টেস্ট", "phone": "01812345678", "address": "ঢাকা শহর",
            "delivery_zone": "inside_dhaka", "payment_method": "cod",
            "items": [{"type": "combo", "id": "exam-kit-combo", "qty": 1, "price": 1, "unit_price": 1}]
        }
        res = self.client.post("/api/orders", json=payload)
        self.assertEqual(res.get_json()["total_amount"], 160.0)

    def test_bad_quantities_do_not_crash(self):
        payload = {
            "customer_name": "টেস্ট", "phone": "01812345678", "address": "ঢাকা শহর",
            "delivery_zone": "inside_dhaka", "payment_method": "cod",
            "items": [{"type": "combo", "id": "exam-kit-combo", "qty": "abc"}]
        }
        self.assertEqual(self.client.post("/api/orders", json=payload).status_code, 400)
        payload["items"] = [{"type": "combo", "id": "exam-kit-combo", "qty": 9999}]
        res = self.client.post("/api/orders", json=payload)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["total_amount"], 100.0 * 50 + 60.0)  # capped at 50

    def test_phone_validation_rejection(self):
        payload = {
            "customer_name": "ভুল নম্বর টেস্ট",
            "phone": "012345",
            "address": "ঢাকা শহর",
            "delivery_zone": "inside_dhaka",
            "payment_method": "cod",
            "items": [{"type": "combo", "id": "exam-kit-combo", "qty": 1}]
        }
        self.assertEqual(self.client.post("/api/orders", json=payload).status_code, 400)

    def test_minimum_three_char_address_acceptance(self):
        payload_valid = {
            "customer_name": "সিয়াম ইসলাম",
            "phone": "01939707648",
            "address": "ইবি",
            "delivery_zone": "kushtia_iu",
            "payment_method": "cod",
            "items": [{"type": "combo", "id": "exam-kit-combo", "qty": 1}]
        }
        self.assertEqual(self.client.post("/api/orders", json=payload_valid).status_code, 201)

        payload_invalid = dict(payload_valid, address="ঢাকা"[:2], delivery_zone="inside_dhaka")
        res_invalid = self.client.post("/api/orders", json=payload_invalid)
        self.assertEqual(res_invalid.status_code, 400)
        self.assertIn("সর্বনিম্ন ৩ অক্ষর", res_invalid.get_json()["message"])

    # ---------------- admin / security ----------------
    def test_admin_requires_login_and_new_password_works(self):
        res = self.client.get("/admin")
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.headers["Location"])

        login_res = self.client.post("/admin/login", data={"username": "admin", "password": ADMIN_PASSWORD}, follow_redirects=True)
        self.assertEqual(login_res.status_code, 200)

        dash_res = self.client.get("/admin")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn("কমান্ড সেন্টার", dash_res.data.decode("utf-8"))

        csv_res = self.client.get("/admin/export/csv")
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("text/csv", csv_res.content_type)

    def test_old_default_password_no_longer_works(self):
        res = self.client.post("/admin/login", data={"username": "admin", "password": "primora123"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("ভুল ইউজারনেম বা পাসওয়ার্ড", res.data.decode("utf-8"))
        self.assertEqual(self.client.get("/admin").status_code, 302)

    def test_admin_password_not_stored_in_database(self):
        with get_db() as conn:
            rows = conn.execute("SELECT key FROM settings WHERE key IN ('admin_username', 'admin_password')").fetchall()
        self.assertEqual(len(rows), 0)

    def test_login_lockout_after_repeated_failures(self):
        for _ in range(appmod.LOGIN_MAX_FAILS):
            self.client.post("/admin/login", data={"username": "admin", "password": "wrong"})
        # even the right password is refused while locked
        res = self.client.post("/admin/login", data={"username": "admin", "password": ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 429)

    def test_login_redirect_cannot_leave_the_site(self):
        res = self.client.post("/admin/login?next=https://evil.example/",
                               data={"username": "admin", "password": ADMIN_PASSWORD})
        self.assertEqual(res.status_code, 302)
        self.assertNotIn("evil.example", res.headers["Location"])

    def test_admin_dashboard_shows_ordered_items_and_csv_is_formula_safe(self):
        self.client.post("/api/orders", json={
            "customer_name": "=HYPERLINK(\"http://x\")", "phone": "01812345678", "address": "ঢাকা শহর",
            "delivery_zone": "inside_dhaka", "payment_method": "cod",
            "items": [{"type": "combo", "id": "extra-combo", "qty": 2}]
        })
        self.client.post("/admin/login", data={"username": "admin", "password": ADMIN_PASSWORD})
        dash = self.client.get("/admin").data.decode("utf-8")
        self.assertIn("অর্ডারকৃত পণ্য", dash)
        self.assertIn("×2", dash)
        csv_text = self.client.get("/admin/export/csv").data.decode("utf-8")
        self.assertIn("'=HYPERLINK", csv_text)  # neutralised, will not run as a formula
        self.assertIn("Items", csv_text)

    def test_categories_api_and_settings(self):
        res = self.client.get("/api/categories")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
        self.assertTrue(any(c["slug"] == "stationery" for c in data["categories"]))

        html = self.client.get("/").data.decode("utf-8")
        self.assertIn("Siyam Islam", html)
        self.assertIn("CyBer RDX", html)
        self.assertIn("01939707648", html)
        self.assertIn("01843010908", html)
        self.assertIn("Kushtia Sadar", html)

    # ---------------- e-mail notifications & incomplete checkouts ----------------
    def _order_payload(self, **extra):
        p = {
            "customer_name": "মেইল টেস্ট", "phone": "01712345670", "address": "সাদ্দাম হল ২০৪",
            "delivery_zone": "kushtia_iu", "payment_method": "cod",
            "items": [{"type": "combo", "id": "extra-combo", "qty": 2}],
        }
        p.update(extra)
        return p

    def test_new_order_is_emailed_to_owner(self):
        res = self.client.post("/api/orders", json=self._order_payload())
        self.assertEqual(res.status_code, 201)
        on = res.get_json()["order_number"]
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.tg), 1)            # the same order also goes to Telegram
        self.assertIn(on, self.tg[0])
        subject, body = self.sent[0]
        self.assertIn(on, subject)
        for needle in ("মেইল টেস্ট", "01712345670", "সাদ্দাম হল ২০৪", "×2", "৳260"):
            self.assertIn(needle, body)

    def test_email_failure_never_breaks_the_order(self):
        def boom(subject, body):
            raise RuntimeError("smtp down")
        notify.send_mail = boom
        res = self.client.post("/api/orders", json=self._order_payload(phone="01712345671"))
        self.assertEqual(res.status_code, 201)

    def test_partial_draft_is_saved_and_emailed_when_customer_leaves(self):
        did = "a1b2c3d4e5f60718293a4b5c"
        draft = {"draft_id": did, "customer_name": "রাফি", "phone": "0171234", "items": [{"id": "extra-combo", "qty": 1}]}
        # typing (not final): saved, no e-mail yet
        self.assertEqual(self.client.post("/api/checkout-draft", json=dict(draft, final=False)).status_code, 200)
        self.assertEqual(self.sent, [])
        # customer leaves the page: e-mail + Telegram with exactly what was typed
        self.client.post("/api/checkout-draft", json=dict(draft, final=True))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.tg), 1)
        self.assertIn("রাফি", self.tg[0])
        subject, body = self.sent[0]
        self.assertIn("অসম্পূর্ণ", subject)
        self.assertIn("রাফি", body)
        self.assertIn("0171234", body)
        self.assertIn("এক্সট্রা কম্বো", body)
        # same content again -> no duplicate e-mail
        self.client.post("/api/checkout-draft", json=dict(draft, final=True))
        self.assertEqual(len(self.sent), 1)

    def test_draft_becomes_complete_after_order_and_stops_emailing(self):
        did = "0f1e2d3c4b5a69788796a5b4"
        self.client.post("/api/checkout-draft", json={"draft_id": did, "customer_name": "সম্পূর্ণ", "phone": "01712345672", "final": False})
        res = self.client.post("/api/orders", json=self._order_payload(phone="01712345672", draft_id=did))
        self.assertEqual(res.status_code, 201)
        self.sent.clear()
        # browser leaves the page after the order -> must NOT count as abandoned
        self.client.post("/api/checkout-draft", json={"draft_id": did, "customer_name": "সম্পূর্ণ", "phone": "01712345672", "final": True})
        self.assertEqual(self.sent, [])
        with get_db() as conn:
            row = conn.execute("SELECT status FROM checkout_drafts WHERE draft_id = ?", (did,)).fetchone()
        self.assertEqual(row["status"], "completed")

    def test_empty_or_invalid_drafts_are_ignored(self):
        self.assertEqual(self.client.post("/api/checkout-draft", json={"draft_id": "nope", "customer_name": "abc"}).status_code, 400)
        r = self.client.post("/api/checkout-draft", json={"draft_id": "ffeeddccbbaa99887766", "customer_name": "", "phone": ""})
        self.assertEqual(r.get_json()["status"], "ignored")
        self.assertEqual(self.client.post("/api/checkout-draft", data="not json").status_code, 400)

    def test_admin_sees_incomplete_checkouts_and_can_backup(self):
        self.client.post("/api/checkout-draft", json={"draft_id": "1122334455667788aabb", "customer_name": "অসমাপ্ত গ্রাহক",
                                                      "phone": "01712345673", "items": [{"id": "exam-kit-combo", "qty": 3}]})
        self.assertEqual(self.client.get("/admin/backup").status_code, 302)  # login needed
        self.client.post("/admin/login", data={"username": "admin", "password": ADMIN_PASSWORD})
        dash = self.client.get("/admin").data.decode("utf-8")
        self.assertIn("অসম্পূর্ণ অর্ডার", dash)
        self.assertIn("অসমাপ্ত গ্রাহক", dash)
        self.assertIn("wa.me/8801712345673", dash)
        backup = self.client.get("/admin/backup")
        self.assertEqual(backup.status_code, 200)
        self.assertTrue(backup.data.startswith(b"SQLite format 3"))
        ok = self.client.post("/admin/api/test-notify")
        self.assertEqual(ok.get_json()["status"], "success")
        self.assertTrue(any("টেস্ট" in subj for subj, _ in self.sent))
        self.assertTrue(any("টেস্ট" in t for t in self.tg))

    def _login(self, pw=ADMIN_PASSWORD):
        return self.client.post("/admin/login", data={"username": "admin", "password": pw})

    def test_settings_page_saves_and_never_leaks_secrets(self):
        self.assertEqual(self.client.get("/admin/settings").status_code, 302)   # needs login
        self._login()
        page = self.client.get("/admin/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn("7976148479", page.data.decode("utf-8"))                     # your Telegram chat id is pre-filled
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]
        bad = self.client.post("/admin/settings", data={"csrf": "wrong", "mail_user": "a@b.co"})
        self.assertIn("সেশন মেয়াদ", bad.data.decode("utf-8"))
        ok = self.client.post("/admin/settings", data={
            "csrf": csrf, "mail_user": "primorakit@gmail.com", "mail_to": "primorakit@gmail.com",
            "telegram_chat_id": "7976148479", "mail_app_password": "abcd efgh ijkl mnop",
            "telegram_bot_token": "987654321:AAFsecrettokensecrettokensecrettoken1"})
        self.assertIn("সেভ হয়েছে", ok.data.decode("utf-8"))
        self.assertEqual(notify.cfg()["mail_pass"], "abcdefghijklmnop")
        # secrets must never appear on any public page or in the settings page itself
        for path in ("/", "/cart", "/track"):
            html = self.client.get(path).data.decode("utf-8")
            self.assertNotIn("abcdefghijklmnop", html)
            self.assertNotIn("AAFsecrettoken", html)
        self.assertNotIn("AAFsecrettoken", self.client.get("/admin/settings").data.decode("utf-8"))
        # invalid values are rejected
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]
        bad = self.client.post("/admin/settings", data={"csrf": csrf, "telegram_chat_id": "not-a-number"})
        self.assertIn("Chat ID", bad.data.decode("utf-8"))

    def test_telegram_chat_id_finder(self):
        self._login()
        notify.find_telegram_chats = lambda token: [{"id": "7976148479", "name": "Siyam Islam", "type": "private"}]
        res = self.client.post("/admin/api/telegram-chat", json={"token": "123456789:AAEdummydummydummydummydummydummydum"})
        self.assertEqual(res.get_json()["chats"][0]["id"], "7976148479")

    def test_admin_can_change_password(self):
        self._login()
        self.client.get("/admin/password")
        with self.client.session_transaction() as sess:
            csrf = sess["csrf"]
        self.assertIn("ডিফল্ট পাসওয়ার্ড", self.client.get("/admin").data.decode("utf-8"))
        wrong = self.client.post("/admin/password", data={"csrf": csrf, "current": "nope", "new": "BrandNewPass!234", "new2": "BrandNewPass!234"})
        self.assertIn("বর্তমান পাসওয়ার্ড ভুল", wrong.data.decode("utf-8"))
        short = self.client.post("/admin/password", data={"csrf": csrf, "current": ADMIN_PASSWORD, "new": "short", "new2": "short"})
        self.assertIn("১০ অক্ষর", short.data.decode("utf-8"))
        ok = self.client.post("/admin/password", data={"csrf": csrf, "current": ADMIN_PASSWORD, "new": "BrandNewPass!234", "new2": "BrandNewPass!234"})
        self.assertIn("পাসওয়ার্ড পরিবর্তন হয়েছে", ok.data.decode("utf-8"))
        # the old password stops working, the new one works, and the default-password warning is gone
        self.client.get("/admin/logout")
        self.assertEqual(self._login().status_code, 200)              # old password -> login page again
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.assertEqual(self._login("BrandNewPass!234").status_code, 302)
        self.assertNotIn("ডিফল্ট পাসওয়ার্ড", self.client.get("/admin").data.decode("utf-8"))

    def test_english_texts_exist_for_combos(self):
        combos = self.client.get("/api/combos").get_json()["combos"]
        for c in combos:
            self.assertTrue(c["en_tagline"].isascii() or "৳" in c["en_tagline"])
            self.assertTrue(c["en_name"].isascii())
            for it in c["items_breakdown"]:
                self.assertTrue(it["en_name"].isascii(), it["en_name"])

    def test_times_are_shown_in_bangladesh_time(self):
        self.assertEqual(appmod.to_bd_time("2026-09-20 18:25:11"), "2026-09-21 00:25")


if __name__ == "__main__":
    unittest.main()
