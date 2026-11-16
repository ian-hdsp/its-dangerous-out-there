"""Simulate a stock drop end to end and measure how fast the push goes out.

Runs the real check loop (check_stock.main) in headless Chromium against a local mock of the
Nintendo product page, plus a local mock ntfy server that records when each push arrives.
The mock page shows "Sold out", switches to "Add to cart" at --in-at seconds, then back to
"Sold out" at --out-at. Nothing touches the real store or your real ntfy topic.

Pass/fail (exit code 1 on failure):
  - exactly one urgent IN STOCK push and one low-priority "sold out again" push
  - each push arrives within one check interval + check time (+ slack) of the page change

Usage (needs Playwright + Chromium: pip install -r requirements.txt && python -m playwright install chromium):
    python tools/simulate_drop.py                 # current settings: check every 60 s, ~4 min
    python tools/simulate_drop.py --interval 30 --minutes 2.5   # quicker run

The defaults put the change part-way between two checks, like a real drop would.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def page_html(in_stock: bool) -> str:
    """Mock product page: buy area next to the h1, plus a related-products 'Add to cart'
    that must be ignored (as on the real page)."""
    btn = "<button>Add to cart</button>" if in_stock else "<button disabled>Sold out</button>"
    return ("<html><body><main><section><h1>Nintendo Switch 2 - Zelda 40th Anniversary</h1>"
            f'<div class="price">$519.99</div><div class="actions">{btn}</div></section>'
            "<aside><h2>Related</h2><button>Add to cart</button></aside></main></body></html>")


def run(interval: float, minutes: float, in_at: float, out_at: float, page_delay: float) -> int:
    t0 = [0.0]
    pushes: list[tuple[float, str, str]] = []

    class Store(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            t = time.time() - t0[0]
            time.sleep(page_delay)  # the real page takes ~5 s per check (GitHub step timings)
            body = page_html(in_at <= t < out_at).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    class Ntfy(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            pushes.append((time.time() - t0[0], self.headers["Title"], self.headers["Priority"]))
            self.send_response(200)
            self.end_headers()

    def serve(handler) -> int:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv.server_address[1]

    store_port, ntfy_port = serve(Store), serve(Ntfy)
    state = os.path.join(tempfile.mkdtemp(prefix="drop-sim-"), "state.json")
    os.environ.update(
        NTFY_TOPIC="simulation", NTFY_SERVER=f"http://127.0.0.1:{ntfy_port}", STATE_FILE=state,
        LOOP_MINUTES=str(minutes), CHECK_INTERVAL=str(int(interval)),
        NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
    )
    for k in ("DRY_RUN", "TEST_RUN", "DEBUG", "NOTIFY_ON_UNKNOWN", "GITHUB_STEP_SUMMARY"):
        os.environ.pop(k, None)

    import check_stock

    check_stock.PRODUCTS = [{"retailer": "Nintendo Store", "url": f"http://127.0.0.1:{store_port}/product"}]
    print(f"Simulating: check every {interval:.0f} s for {minutes} min; page in stock "
          f"from t={in_at:.0f}s to t={out_at:.0f}s (page load {page_delay}s)\n", flush=True)
    t0[0] = time.time()
    check_stock.main()

    print("\n--- pushes (seconds after the loop started) ---")
    limit = interval + page_delay + 10  # worst case: change just after a check started
    failures = []
    in_pushes = [p for p in pushes if "IN STOCK" in p[1]]
    out_pushes = [p for p in pushes if "sold out" in p[1]]
    for t, title, prio in pushes:
        ref = in_at if "IN STOCK" in title else out_at
        print(f"t={t:6.1f}s  [{prio:6}] {title}  -> {t - ref:.1f} s after the page changed")
    if len(in_pushes) != 1:
        failures.append(f"expected 1 IN STOCK push, got {len(in_pushes)}")
    elif in_pushes[0][2] != "urgent":
        failures.append("IN STOCK push was not urgent")
    elif in_pushes[0][0] - in_at > limit:
        failures.append(f"IN STOCK push took {in_pushes[0][0] - in_at:.1f}s (limit {limit:.0f}s)")
    if len(out_pushes) != 1:
        failures.append(f"expected 1 'sold out again' push, got {len(out_pushes)}")
    elif out_pushes[0][0] - out_at > limit:
        failures.append(f"sold-out push took {out_pushes[0][0] - out_at:.1f}s (limit {limit:.0f}s)")
    if len(pushes) != 2:
        failures.append(f"expected exactly 2 pushes in total, got {len(pushes)}")

    if failures:
        print("\nFAIL:\n  " + "\n  ".join(failures))
        return 1
    print(f"\nPASS: 2 pushes, each within {limit:.0f} s of the page change")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=60, help="seconds between checks (min 30; default 60)")
    ap.add_argument("--minutes", type=float, default=4, help="how long the loop runs (default 4)")
    ap.add_argument("--in-at", type=float, help="seconds until the page shows 'Add to cart' "
                    "(default: 1.6 intervals in, i.e. part-way between two checks)")
    ap.add_argument("--out-at", type=float, help="seconds until it's 'Sold out' again (default: 2.5 intervals)")
    ap.add_argument("--page-delay", type=float, default=4.5, help="mock page load time in s (default 4.5)")
    a = ap.parse_args()
    interval = max(30.0, a.interval)  # check_stock enforces the same floor
    in_at = a.in_at if a.in_at is not None else 1.6 * interval
    out_at = a.out_at if a.out_at is not None else 2.5 * interval
    if not (0 < in_at < out_at < a.minutes * 60 - interval):
        ap.error("need 0 < --in-at < --out-at, with at least one check after --out-at before the loop ends")
    return run(interval, a.minutes, in_at, out_at, a.page_delay)


if __name__ == "__main__":
    sys.exit(main())
