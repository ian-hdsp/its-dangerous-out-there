"""Run on your own computer: when the watcher pushes "IN STOCK", open the product page
in your default (already signed-in) browser right away and play a sound.

It only opens the page; you add to cart and check out yourself.

Usage (Python 3.9+, no extra packages):
    python desktop_alert.py YOUR_NTFY_TOPIC
    python desktop_alert.py YOUR_NTFY_TOPIC --test   # open the page once now, to check it works

Keep the topic out of the repo (it's public): pass it on the command line or set NTFY_TOPIC.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
import webbrowser

PRODUCT_URL = ("https://www.nintendo.com/us/store/products/"
               "nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition-121642/")


def is_in_stock_alert(msg: dict) -> bool:
    """True for the watcher's real IN STOCK push: tagged "in_stock", not a sold-out push, and
    not a manual test run (tagged "test_tube", title "[TEST] ..."), which never opens the browser."""
    tags = msg.get("tags") or []
    return msg.get("event") == "message" and "in_stock" in tags and "test_tube" not in tags


def alarm() -> None:
    """Best-effort sound so you notice even if the browser opens behind other windows."""
    try:
        system = platform.system()
        if system == "Darwin":
            for _ in range(3):
                subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
        elif system == "Windows":
            import winsound
            for _ in range(3):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                time.sleep(0.4)
        else:
            print("\a\a\a", end="", flush=True)
    except Exception:
        print("\a", end="", flush=True)


MAX_AGE_SECS = 10 * 60  # after sleep/wake, don't open the page for a drop that's long over


def handle(msg: dict) -> None:
    if not is_in_stock_alert(msg):
        if msg.get("event") == "message":
            print(f"{time.strftime('%H:%M:%S')} (ignored) {msg.get('title', '')}", flush=True)
        return
    if msg.get("time") and time.time() - msg["time"] > MAX_AGE_SECS:
        print(f"{time.strftime('%H:%M:%S')} (stale, ignored) {msg.get('title', '')}", flush=True)
        return
    url = msg.get("click") or PRODUCT_URL
    print(f"{time.strftime('%H:%M:%S')} IN STOCK -> opening {url}", flush=True)
    webbrowser.open(url, new=2)  # new tab in your default browser
    alarm()


def listen(server: str, topic: str) -> None:
    """Stream the topic forever, reconnecting after network drops or sleep/wake."""
    url = f"{server.rstrip('/')}/{topic}/json"
    since = None
    seen: set[str] = set()  # never act on the same message twice, even if a reconnect replays it
    while True:
        try:
            # since=<id> after a reconnect so a push sent while disconnected isn't missed
            full = url + (f"?since={since}" if since else "")
            with urllib.request.urlopen(full, timeout=90) as resp:  # ntfy sends keepalives ~45s
                print(f"{time.strftime('%H:%M:%S')} listening on {server}/<topic>", flush=True)
                for line in resp:
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    if msg.get("event") == "message" and msg.get("id"):
                        if msg["id"] in seen:
                            continue
                        seen.add(msg["id"])
                        since = msg["id"]
                    handle(msg)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} connection lost ({e}); retrying in 5s", flush=True)
            time.sleep(5)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("topic", nargs="?", default=os.environ.get("NTFY_TOPIC"), help="your ntfy topic")
    ap.add_argument("--server", default=os.environ.get("NTFY_SERVER", "https://ntfy.sh"))
    ap.add_argument("--test", action="store_true", help="open the product page + sound once, then exit")
    args = ap.parse_args()
    if args.test:
        handle({"event": "message", "tags": ["in_stock"], "click": PRODUCT_URL})
        return 0
    if not args.topic:
        ap.error("pass your ntfy topic (or set NTFY_TOPIC)")
    try:
        listen(args.server, args.topic)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
