"""English versions of the texts that come from the database (combo names, taglines, item names ...).
The website uses them when the visitor switches the language to EN.
If a text is missing here the Bengali text is simply shown, so nothing ever breaks."""

COMBO_EN = {
    "exam-kit-combo": {
        "name": "Exam Kit Combo (8 items)",
        "tagline": "Everything you need in the exam hall, together at a smart price!",
        "badge": "Best Value ৳100",
        "discount": "44% Saved",
    },
    "extra-combo": {
        "name": "Extra Combo (8+3 = 11 items)",
        "tagline": "On top of the 8 items you also get 3 more Fresh Shine pens, a net file, a plastic scale and a colour pencil!",
        "badge": "Most Popular ৳130 🔥",
        "discount": "45%+ extra savings!",
    },
}

COMBO_NAME_EN = {
    "এক্সাম কিট কম্বো (৮টি পণ্য)": "Exam Kit Combo (8 items)",
    "এক্সট্রা কম্বো (৮+৩ = ১১টি পণ্য)": "Extra Combo (8+3 = 11 items)",
}

ITEM_EN = {
    "Apple Nion 2B পেন্সিল (Indian)": "Apple Nion 2B Pencil (Indian)",
    "Fresh Shine পেন (Bangladeshi)": "Fresh Shine Pen (Bangladeshi)",
    "রাবার হার্ট (Chinese)": "Heart Eraser (Chinese)",
    "ইমোজি ছোট রাবার (Chinese)": "Small Emoji Eraser (Chinese)",
    "Doms শার্পনার (Indian)": "Doms Sharpener (Indian)",
    "মিনি PP স্পাইডারম্যান পেন্সিল ফাইল (Chinese)": "Mini PP Spider-Man Pencil File (Chinese)",
    "কালার ক্যান্ডি পেন্সিল HB (Indian)": "Color Candy Pencil HB (Indian)",
    "Bullet শার্পনার (Indian)": "Bullet Sharpener (Indian)",
    # single products
    "ফ্রেশ শাইন বলপেন": "Fresh Shine Ball Pen",
    "ইমোজি পেন্সিল রাবার": "Emoji Pencil Eraser",
    "ডমস কালার ক্যান্ডি পেন্সিল": "Doms C3 Color Candy HB",
    "ডেলি নিয়ন 2B পেন্সিল": "Deli 2B Neon Black Pencil",
    "প্লাস্টিকের ১ নম্বর ফেমাস স্কেল": "Famous Plastic Scale 1 No",
    "পিএফ পাইলট ফ্লেক্সিবল রবার স্কেল": "Flexible Rubber Scale PF-Pilot",
    "ডমস রোবাস্ট পেন্সিল শার্পনার": "Doms Robust Pencil Sharpener",
    "পেন্সিল ও পেন নেট ব্যাগ": "Pencil and Pen Net Bag",
    "ওজি সাদা বটম ফাইল ট্রান্সপারেন্ট": "Oaji White Bottom Exam File",
}


def en_item(bn_name):
    """English name for an item / combo / product name that is stored in Bengali."""
    return ITEM_EN.get(bn_name) or COMBO_NAME_EN.get(bn_name) or bn_name


def localize_combo(c):
    """Adds en_name / en_tagline / en_badge / en_discount and en_name for every item of a combo dict."""
    en = COMBO_EN.get(c.get("slug"), {})
    c["en_name"] = en.get("name") or c.get("name") or c.get("bn_name")
    c["en_tagline"] = en.get("tagline") or c.get("tagline") or ""
    c["en_badge"] = en.get("badge") or c.get("badge_text") or ""
    c["en_discount"] = en.get("discount") or c.get("discount_text") or ""
    for it in c.get("items_breakdown", []):
        it["en_name"] = en_item(it.get("name"))
    return c
