"""LINE Flex Message cards and quick-reply buttons. Pure functions returning the JSON LINE expects.
Every card is sent together with a plain-text alternative, and notify.py falls back to that text
if LINE rejects a card, so a malformed card can never silence an alert."""
COLORS = {"alert": "#d2372f", "watch": "#c98a00", "normal": "#1b8f61", "unknown": "#6b7f8a"}
LABEL = {"alert": "เตือนภัย", "watch": "เฝ้าระวัง", "normal": "ปกติ", "unknown": "ไม่มีเกณฑ์เทียบ"}
BRAND = "#0e5a78"
MUTED = "#587280"
ARROW = {"rising": "▲ กำลังสูงขึ้น", "falling": "▼ กำลังลดลง", "steady": "■ ทรงตัว"}


def _text(text, **kw):
    return {"type": "text", "text": text, "wrap": True, **kw}


def _gauge(pct, color):
    w = max(1, min(100, round(pct or 0)))  # Flex needs 1%..100%
    return {"type": "box", "layout": "vertical", "height": "8px", "backgroundColor": "#d3dfe5", "cornerRadius": "4px",
            "contents": [{"type": "box", "layout": "vertical", "width": f"{w}%", "height": "8px",
                          "backgroundColor": color, "cornerRadius": "4px", "contents": []}]}


def _footer(url, label="ดูรายงานจังหวัด"):
    if not url or not url.startswith("https://"):
        return None
    return {"type": "box", "layout": "vertical", "contents": [
        {"type": "button", "style": "primary", "color": BRAND, "height": "sm",
         "action": {"type": "uri", "label": label, "uri": url}}]}


def _bubble(header, body, footer=None):
    b = {"type": "bubble", "size": "kilo", "header": header, "body": body}
    if footer:
        b["footer"] = footer
    return b


def station_card(label, st, url=None):
    """Alert / status card for the station nearest a subscriber."""
    color = COLORS.get(st["status"], COLORS["unknown"])
    pct = st.get("pct_of_bank")
    header = {"type": "box", "layout": "vertical", "backgroundColor": color, "paddingAll": "12px", "contents": [
        _text(f"น้ำใกล้บ้านฉัน{' · ' + label if label else ''}", color="#ffffff", size="xs"),
        _text(LABEL.get(st["status"], ""), color="#ffffff", weight="bold", size="lg")]}
    body = [_text(st["name"], weight="bold", size="md"),
            _text(f"{st.get('province') or ''} · ห่าง {st['distance_km']} กม.", size="xs", color=MUTED),
            _text("-" if pct is None else f"{round(pct)}% ของตลิ่ง", weight="bold", size="xxl", color=color, margin="md")]
    if pct is not None:
        body.append(_gauge(pct, color))
    if st.get("trend") in ARROW:
        rate = st.get("trend_pct_per_hr")
        eta = f" · ถึงตลิ่งใน ~{st['eta_to_bank_h']} ชม." if st.get("eta_to_bank_h") else ""
        body.append(_text(f"{ARROW[st['trend']]} {rate:+.1f}%/ชม.{eta}" if rate is not None else ARROW[st["trend"]],
                          size="sm", color=color if st["trend"] == "rising" else MUTED, margin="sm"))
    if (st.get("rise_3h_m") or 0) >= 0.3:
        body.append(_text(f"▲ ระดับน้ำขึ้น +{st['rise_3h_m']:.2f} ม. ใน 3 ชม.", size="sm", color=color, margin="sm"))
    if st.get("twin_conflict"):
        body.append(_text("⚠ อีกหน่วยงานรายงานสถานะต่างกัน ควรตรวจกับหน่วยงานในพื้นที่", size="xs", color=COLORS["watch"]))
    body.append(_text(st.get("advice", ""), size="sm", color="#333333", margin="md"))
    return _bubble(header, {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}, _footer(url))


def digest_card(rep, when_text, url=None):
    """Morning province report card."""
    c = rep["counts"]
    kpis = [{"type": "box", "layout": "vertical", "flex": 1, "contents": [
        _text(str(c[k]), weight="bold", size="xxl", color=COLORS[k], align="center"),
        _text(LABEL[k], size="xs", color=MUTED, align="center")]} for k in ("alert", "watch", "normal")]
    header = {"type": "box", "layout": "vertical", "backgroundColor": BRAND, "paddingAll": "12px", "contents": [
        _text("สรุปสถานการณ์น้ำ", color="#ffffff", size="xs"),
        _text(f"จ.{rep['province']}", color="#ffffff", weight="bold", size="lg"),
        _text(when_text, color="#d3dfe5", size="xxs")]}
    body = [{"type": "box", "layout": "horizontal", "contents": kpis}]
    d = rep.get("delta_24h")
    notes = []
    if d:
        sign = lambda n: f"+{n}" if n > 0 else str(n)
        notes.append(f"เทียบ 24 ชม. ก่อน: เตือนภัย {sign(d['alert'])} · เฝ้าระวัง {sign(d['watch'])}")
    if rep.get("over_bank"):
        notes.append(f"ล้นตลิ่งแล้ว {rep['over_bank']} สถานี")
    if rep.get("stale"):
        notes.append(f"ข้อมูลค้าง {rep['stale']} สถานี")
    body += [_text(n, size="xs", color=MUTED) for n in notes]
    if rep.get("rain"):
        import rainalert
        body.append(_text(rainalert.line(rep["rain"]), size="xs", color=COLORS["watch"], weight="bold"))
    if rep.get("gauge"):
        import gauges
        body.append(_text(gauges.line(rep["gauge"]), size="xs", color=COLORS["watch"], weight="bold"))
    if rep.get("top"):
        body.append(_text("น้ำสูงสุด", size="sm", weight="bold", margin="md"))
        for r in rep["top"][:5]:
            col = COLORS.get(r["status"], COLORS["unknown"])
            body.append({"type": "box", "layout": "horizontal", "contents": [
                _text(r["name"], size="sm", flex=4), _text(f"{round(r['pct_of_bank'])}%", size="sm", flex=1,
                                                           align="end", weight="bold", color=col)]})
    return _bubble(header, {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body}, _footer(url))


def quick(labels):
    """Quick-reply buttons: tapping one sends its label as a message (LINE allows <= 13, label <= 20 chars)."""
    return [{"type": "action", "action": {"type": "message", "label": l[:20], "text": l}} for l in labels[:13]]


def locate_button(label="ส่งตำแหน่งจุดที่ท่วม"):
    """Quick-reply button that opens LINE's location picker."""
    return {"type": "action", "action": {"type": "location", "label": label[:20]}}


def quick_postback(items):
    """Quick-reply buttons that send hidden data back to the webhook: items = [(label, data)].
    The label is also shown in the chat as the user's own message."""
    return [{"type": "action", "action": {"type": "postback", "label": l[:20], "data": d, "displayText": l[:300]}}
            for l, d in items[:13]]
