#!/usr/bin/env python3
"""Create the rich menu on the LINE official account and make it the default for everyone.

  export LINE_CHANNEL_TOKEN='...'          # type it yourself in your own terminal
  python richmenu/setup_richmenu.py        # create + upload image + set default
  python richmenu/setup_richmenu.py --delete-old   # also remove earlier menus named namklaiban-main

The token is read from the environment only; this script never prints it.
"""
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from menu import build_menu

API, DATA = "https://api.line.me/v2/bot", "https://api-data.line.me/v2/bot"
IMAGE = os.path.join(os.path.dirname(__file__), "richmenu.png")


def call(method, url, token, body=None, content_type="application/json"):
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", **({"Content-Type": content_type} if data is not None else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete-old", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("LINE_CHANNEL_TOKEN", "")
    if not token or not token.isascii() or " " in token:
        sys.exit("Set LINE_CHANNEL_TOKEN to the real channel access token first (no spaces).")
    if os.path.getsize(IMAGE) > 1024 * 1024:
        sys.exit("richmenu.png is over LINE's 1 MB limit")
    menu = build_menu()
    old = [m["richMenuId"] for m in call("GET", f"{API}/richmenu/list", token).get("richmenus", [])
           if m.get("name") == menu["name"]]
    new_id = call("POST", f"{API}/richmenu", token, menu)["richMenuId"]
    call("POST", f"{DATA}/richmenu/{new_id}/content", token, open(IMAGE, "rb").read(), "image/png")
    call("POST", f"{API}/user/all/richmenu/{new_id}", token)
    print(f"rich menu {new_id} created and set as the default for all users")
    if a.delete_old:
        for rid in old:
            call("DELETE", f"{API}/richmenu/{rid}", token)
        print(f"deleted {len(old)} older menu(s)")


if __name__ == "__main__":
    main()
