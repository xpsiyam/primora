import json
from database import get_db, init_db

def seed_database():
    init_db()
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Check combos
        combo_count = cursor.execute("SELECT COUNT(*) FROM combos").fetchone()[0]
        if combo_count == 0:
            exam_kit_items = [
                {"icon": "📁", "name": "Oaji Sada Bottom Examfile (Chinese)", "qty": 1, "market_price": 40},
                {"icon": "📏", "name": "Rubber Scale Pf-Pilot (Chinese)", "qty": 1, "market_price": 50},
                {"icon": "✏️", "name": "Apple Nion 2B পেন্সিল (Indian)", "qty": 1, "market_price": 20},
                {"icon": "🖊️", "name": "Fresh Shine পেন (Bangladeshi)", "qty": 2, "market_price": 15},
                {"icon": "💖", "name": "রাবার হার্ট (Chinese)", "qty": 1, "market_price": 10},
                {"icon": "😊", "name": "ইমোজি ছোট রাবার (Chinese)", "qty": 1, "market_price": 5},
                {"icon": "🔪", "name": "Doms শার্পনার (Indian)", "qty": 1, "market_price": 10},
                {"icon": "👝", "name": "মিনি PP স্পাইডারম্যান পেন্সিল ফাইল (Chinese)", "qty": 1, "market_price": 30}
            ]
            
            extra_combo_items = exam_kit_items + [
                {"icon": "🖊️", "name": "Fresh Shine পেন (Bangladeshi)", "qty": 1, "market_price": 5},
                {"icon": "🗂️", "name": "Net Pencil Box / PP File (Indian)", "qty": 1, "market_price": 30},
                {"icon": "📐", "name": "1 No Plastic Famous Scale (Chinese)", "qty": 1, "market_price": 30},
                {"icon": "🎨", "name": "কালার ক্যান্ডি পেন্সিল HB (Indian)", "qty": 1, "market_price": 10},
                {"icon": "⚙️", "name": "Bullet শার্পনার (Indian)", "qty": 1, "market_price": 15}
            ]

            cursor.execute("""
                INSERT INTO combos (slug, name, bn_name, tagline, price, original_price, discount_text, badge_text, items_breakdown_json, category_id, is_featured, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1)
            """, (
                "exam-kit-combo",
                "Exam Kit Combo (8 items)",
                "এক্সাম কিট কম্বো (৮টি পণ্য)",
                "পরীক্ষার হলের সকল প্রয়োজনীয় সামগ্রী একসাথে সাশ্রয়ী মূল্যে!",
                100.0, 180.0, "৪৪% সাশ্রয়", "বেস্ট ভ্যালু ৳১০০",
                json.dumps(exam_kit_items, ensure_ascii=False)
            ))

            cursor.execute("""
                INSERT INTO combos (slug, name, bn_name, tagline, price, original_price, discount_text, badge_text, items_breakdown_json, category_id, is_featured, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 1)
            """, (
                "extra-combo",
                "Extra Combo (11 items)",
                "এক্সট্রা কম্বো (৮+৩ = ১১টি পণ্য)",
                "উপরের ৮টির সাথে আরও পাচ্ছেন ৩টি ফ্রেশ শাইন পেন, নেট ফাইল, প্লাস্টিক স্কেল ও কালার পেন্সিল!",
                130.0, 240.0, "৪৫%+ অতিরিক্ত সাশ্রয়!", "সবচেয়ে জনপ্রিয় ৳১৩০ 🔥",
                json.dumps(extra_combo_items, ensure_ascii=False)
            ))

        # Check products
        prod_count = cursor.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        if prod_count == 0:
            products = [
                ("fresh-shine-pen", "Fresh Shine Ball Pen", "ফ্রেশ শাইন বলপেন", 6.0, 8.0, 1),
                ("emoji-eraser", "Emoji Pencil Eraser", "ইমোজি পেন্সিল রাবার", 5.0, 6.0, 1),
                ("doms-color-candy", "Doms C3 Color Candy HB", "ডমস কালার ক্যান্ডি পেন্সিল", 10.0, 11.0, 1),
                ("deli-neon-pencil", "Deli 2B Neon Black Pencil", "ডেলি নিয়ন 2B পেন্সিল", 20.0, 22.5, 1),
                ("famous-plastic-scale", "Famous Plastic Scale 1 No", "প্লাস্টিকের ১ নম্বর ফেমাস স্কেল", 30.0, 35.0, 1),
                ("pf-pilot-rubber-scale", "Flexible Rubber Scale PF-Pilot", "পিএফ পাইলট ফ্লেক্সিবল রবার স্কেল", 50.0, 60.0, 1),
                ("doms-sharpener", "Doms Robust Pencil Sharpener", "ডমস রোবাস্ট পেন্সিল শার্পনার", 10.0, 12.0, 1),
                ("pencil-pen-net-bag", "Pencil and Pen Net Bag", "পেন্সিল ও পেন নেট ব্যাগ", 30.0, 35.0, 1),
                ("transparent-bottom-file", "Oaji White Bottom Exam File", "ওজি সাদা বটম ফাইল ট্রান্সপারেন্ট", 40.0, 45.0, 1)
            ]
            for slug, name, bn_name, u_price, m_price, cat_id in products:
                cursor.execute("""
                    INSERT INTO products (slug, name, bn_name, unit_price, market_price, category_id, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (slug, name, bn_name, u_price, m_price, cat_id))

if __name__ == "__main__":
    seed_database()
    print("Database seeded successfully.")
