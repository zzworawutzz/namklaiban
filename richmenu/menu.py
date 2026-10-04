"""Rich menu definition (the 4 big buttons under the chat box). Pure data so it can be tested offline."""
W, H, COLS = 2500, 843, 4
CHAT_BAR_TEXT = "น้ำใกล้บ้านฉัน"   # LINE allows at most 14 characters
LABELS = ["ส่งตำแหน่ง", "สถานะ", "รายงาน", "ตั้งค่า"]


def build_menu():
    col_w = W // COLS
    actions = [
        {"type": "uri", "uri": "https://line.me/R/nv/location"},   # opens LINE's "share location" screen
        {"type": "message", "text": "สถานะ"},
        {"type": "message", "text": "รายงาน"},
        {"type": "message", "text": "ตั้งค่า"},
    ]
    return {"size": {"width": W, "height": H}, "selected": True, "name": "namklaiban-main",
            "chatBarText": CHAT_BAR_TEXT,
            "areas": [{"bounds": {"x": i * col_w, "y": 0, "width": col_w if i < COLS - 1 else W - col_w * (COLS - 1),
                                  "height": H}, "action": a} for i, a in enumerate(actions)]}
