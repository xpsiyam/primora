import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("PRIMORA_DB_PATH", os.path.join(os.path.dirname(__file__), "primora.db"))

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn

@contextmanager
def get_db():
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        # 1. Categories Table (Hierarchical / Extensible)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            bn_name TEXT NOT NULL,
            icon TEXT DEFAULT '📁',
            parent_id INTEGER DEFAULT NULL REFERENCES categories(id) ON DELETE CASCADE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 2. Combos Table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS combos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            bn_name TEXT NOT NULL,
            tagline TEXT,
            description TEXT,
            price REAL NOT NULL,
            original_price REAL NOT NULL,
            discount_text TEXT,
            badge_text TEXT,
            image_url TEXT,
            items_breakdown_json TEXT NOT NULL,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            is_featured INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 3. Products Table (Individual items)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            bn_name TEXT NOT NULL,
            description TEXT,
            unit_price REAL NOT NULL,
            market_price REAL NOT NULL,
            stock_quantity INTEGER NOT NULL DEFAULT 100,
            image_url TEXT,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 4. Orders Table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            alternative_phone TEXT,
            address TEXT NOT NULL,
            division TEXT DEFAULT '',
            district TEXT DEFAULT '',
            delivery_zone TEXT NOT NULL, -- 'kushtia_iu', 'inside_dhaka', 'outside_dhaka'
            campus_details TEXT DEFAULT '',
            delivery_fee REAL NOT NULL DEFAULT 0.0,
            subtotal REAL NOT NULL,
            total_amount REAL NOT NULL,
            payment_method TEXT NOT NULL DEFAULT 'cod', -- 'cod', 'bkash', 'nagad'
            trx_id TEXT,
            payment_status TEXT NOT NULL DEFAULT 'unpaid', -- 'unpaid', 'paid', 'refunded'
            order_notes TEXT,
            status TEXT NOT NULL DEFAULT 'Pending', -- 'Pending', 'Confirmed', 'Processing', 'Shipped', 'Delivered', 'Cancelled'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 5. Order Items Table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL, -- 'combo' or 'product'
            item_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            unit_price REAL NOT NULL,
            quantity INTEGER NOT NULL CHECK(quantity > 0),
            subtotal REAL NOT NULL
        );
        """)

        # 6. System Settings Table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            description TEXT
        );
        """)

        # 7. Checkout drafts: whatever a customer typed before finishing (or abandoning) an order
        conn.execute("""
        CREATE TABLE IF NOT EXISTS checkout_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL DEFAULT 'cart',
            customer_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            alternative_phone TEXT DEFAULT '',
            address TEXT DEFAULT '',
            district TEXT DEFAULT '',
            delivery_zone TEXT DEFAULT '',
            payment_method TEXT DEFAULT 'cod',
            order_notes TEXT DEFAULT '',
            items_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'open', -- 'open' or 'completed'
            order_number TEXT,
            content_hash TEXT DEFAULT '',
            last_emailed_at REAL,
            last_emailed_hash TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drafts_status ON checkout_drafts(status, updated_at);")

        # Ensure category_id column exists if tables were already created previously
        for tbl in ["combos", "products"]:
            cols = [col[1] for col in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if "category_id" not in cols:
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL;")
                except Exception:
                    pass

        # Indices for optimal query performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_categories_slug ON categories(slug);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_phone ON orders(phone);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);")

        # Seed initial categories if empty
        cat_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        if cat_count == 0:
            # Active root category: Stationery
            conn.execute("""
                INSERT INTO categories (id, slug, name, bn_name, icon, parent_id, sort_order, is_active)
                VALUES (1, 'stationery', 'Stationery', 'স্টেশনারি সামগ্রী', '✏️', NULL, 1, 1)
            """)
            # Future root categories (configured in backend for seamless scalability)
            conn.execute("""
                INSERT INTO categories (id, slug, name, bn_name, icon, parent_id, sort_order, is_active)
                VALUES (2, 'gadgets', 'Phone & Gadgets', 'ফোন ও গ্যাজেটস', '📱', NULL, 2, 0)
            """)
            conn.execute("""
                INSERT INTO categories (id, slug, name, bn_name, icon, parent_id, sort_order, is_active)
                VALUES (3, 'lifestyle', 'Student Lifestyle', 'লাইফস্টাইল ও ব্যাগ', '🎒', NULL, 3, 0)
            """)

            # Subcategories under Stationery (parent_id = 1)
            subcategories = [
                ('exam-combos', 'Exam Kit Combos', 'পরীক্ষার কম্বো কিট', '📦', 1, 1),
                ('pens', 'Ballpoint Pens', 'বলপেন ও হাইলাইটার', '🖊️', 1, 2),
                ('pencils', 'Pencils & Leads', 'পেন্সিল ও নিয়ন লেড', '✏️', 1, 3),
                ('erasers-sharpeners', 'Erasers & Sharpeners', 'রাবার ও শার্পনার', '🧼', 1, 4),
                ('files-scales', 'Exam Files & Scales', 'ফাইল ও স্কেল', '📁', 1, 5)
            ]
            for slug, name, bn_name, icon, parent_id, sort_order in subcategories:
                conn.execute("""
                    INSERT INTO categories (slug, name, bn_name, icon, parent_id, sort_order, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (slug, name, bn_name, icon, parent_id, sort_order))

        # Seed / Update Settings
        app_settings = {
            "site_name": "PRIMORA KIT",
            "tagline": "Pay Less. Get Premium.",
            "kushtia_iu_fee": "0",
            "inside_dhaka_fee": "60",
            "outside_dhaka_fee": "120",
            "support_phone": "01843010908",
            "whatsapp_number": "8801843010908",
            "facebook_page": "https://www.facebook.com/primorakit",
            "founder_name": "Siyam Islam",
            "founder_title": "CEO & Founder, PRIMORA KIT",
            "founder_edu": "CSE Student at Islamic University (IU)",
            "founder_role": "Tutor of CyBer RDX",
            "founder_photo": "/static/images/siyam_islam.jpg",
            "founder_fb": "https://www.facebook.com/siyam.islam.643583",
            "founder_phone": "01939707648",
            "bkash_number": "01939707648",
            "nagad_number": "01939707648",
            "address": "Kushtia Sadar, Khulna"
        }

        for key, val in app_settings.items():
            conn.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """, (key, str(val)))

        # Notification settings: added only when missing, so anything you save in the admin panel is kept.
        soft_defaults = {
            "mail_user": "primorakit@gmail.com",
            "mail_to": "primorakit@gmail.com",
            "telegram_chat_id": "7976148479",
        }
        for key, val in soft_defaults.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

        # SECURITY: admin credentials must never live in the database.
        # (Older versions stored "admin_password" here in plain text.)
        # Remove them from any existing primora.db. The admin password is now
        # a hash configured in app.py / the ADMIN_PASSWORD_HASH env variable.
        conn.execute("DELETE FROM settings WHERE key IN ('admin_username', 'admin_password')")

def get_category_tree():
    """Returns categories in a nested tree structure for mega-menus."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM categories 
            ORDER BY parent_id IS NOT NULL, sort_order ASC, id ASC
        """).fetchall()

    categories_map = {row["id"]: dict(row, children=[]) for row in rows}
    tree = []
    for cat_id, cat in categories_map.items():
        if cat["parent_id"] is None:
            tree.append(cat)
        elif cat["parent_id"] in categories_map:
            categories_map[cat["parent_id"]]["children"].append(cat)
    return tree

if __name__ == "__main__":
    init_db()
    print("Database schema and settings initialized successfully.")
