"""Check the Nintendo Store for the Zelda 40th Anniversary Switch 2 console.

Runs headless Chromium (Playwright), decides IN_STOCK / OUT_OF_STOCK / UNKNOWN per
retailer, and sends an urgent ntfy.sh push when anything is IN_STOCK.

Env vars:
  NTFY_TOPIC        required to send pushes (keep it secret; anyone with it can read them)
  NTFY_SERVER       optional, default https://ntfy.sh
  NOTIFY_ON_UNKNOWN optional "1" -> also push when a page can't be read (e.g. bot-blocked)
  DRY_RUN           optional "1" -> print instead of sending
  TEST_RUN          optional "1" -> label every push "[TEST]" with a test-tube tag (manual runs)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import requests

IN_STOCK, OUT_OF_STOCK, UNKNOWN = "IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"

# Target (TCIN 1013322047) is not checked: its bot protection refuses GitHub-hosted runners
# (stock API returns HTTP 435), so it can only ever report UNKNOWN from here. Target is covered
# by a separate hourly restock-tracker alert. The Target parsing code below is kept in case a
# runner with a residential IP is added later; re-add this entry to enable it:
#   {"retailer": "Target", "url": "https://www.target.com/p/-/A-1013322047", "tcin": "1013322047"},
PRODUCTS = [
    {
        "retailer": "Nintendo Store",
        "url": "https://www.nintendo.com/us/store/products/"
        "nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition-121642/",
    },
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BUY_WORDS = re.compile(r"\b(add to cart|ship it|buy now|pick it up|deliver it)\b", re.I)
SOLD_OUT_WORDS = re.compile(
    r"\b(sold out|out of stock|currently unavailable|notify me|coming soon|unavailable)\b", re.I
)
BLOCK_WORDS = re.compile(
    r"(access denied|are you a human|verify you are human|captcha|request blocked|"
    r"unusual traffic|pardon our interruption)",
    re.I,
)

# Target fulfillment API values (from the JSON the product page itself loads).
TARGET_IN = {"IN_STOCK", "LIMITED_STOCK", "PRE_ORDER_SELLABLE"}
TARGET_OUT = {"OUT_OF_STOCK", "UNAVAILABLE", "DISCONTINUED", "PRE_ORDER_UNSELLABLE"}


@dataclass
class Evidence:
    """Everything scraped from one page; decide() turns it into a status."""

    http_status: int | None = None
    page_text_head: str = ""
    jsonld_availability: list[str] = field(default_factory=list)
    target_api_status: list[str] = field(default_factory=list)
    api_http_errors: list[int] = field(default_factory=list)  # Target API calls that were refused
    # Stock-related fields seen in data the Nintendo page loads/embeds: (source, key, value).
    # Recorded to learn which field (if any) shows the real stock when the button says
    # "Add to cart" but the cart says "The requested quantity is not available".
    stock_fields: list[tuple[str, str, str]] = field(default_factory=list)
    main_buttons: list[dict] = field(default_factory=list)  # {"text": str, "disabled": bool}
    price: str | None = None


def _jsonld_status(values: list[str]) -> str | None:
    vals = [v.rsplit("/", 1)[-1].lower() for v in values]
    if any(v in ("instock", "limitedavailability", "preorder", "onlineonly") for v in vals):
        return IN_STOCK
    if vals and all(v in ("outofstock", "soldout", "discontinued", "preordersoldout") for v in vals):
        return OUT_OF_STOCK
    return None


def decide(ev: Evidence) -> tuple[str, str]:
    """Return (status, reason). Conservative: only IN_STOCK on a positive signal."""
    if ev.http_status and ev.http_status >= 400:
        return UNKNOWN, f"HTTP {ev.http_status} (likely bot-blocked)"
    if BLOCK_WORDS.search(ev.page_text_head):
        return UNKNOWN, "bot-check / block page"

    # 1) Target's own fulfillment JSON is the most reliable signal.
    if ev.target_api_status:
        s = {x.upper() for x in ev.target_api_status}
        if s & TARGET_IN:
            return IN_STOCK, f"Target API: {sorted(s)}"
        if s <= TARGET_OUT:
            return OUT_OF_STOCK, f"Target API: {sorted(s)}"

    # 2) Enabled buy button in the main product area.
    enabled_buy = [b for b in ev.main_buttons if BUY_WORDS.search(b["text"]) and not b["disabled"]]
    sold_out = [b for b in ev.main_buttons if SOLD_OUT_WORDS.search(b["text"])]
    jl = _jsonld_status(ev.jsonld_availability)

    if enabled_buy and not sold_out:
        return IN_STOCK, f"buy button: {enabled_buy[0]['text']!r}"
    if sold_out and not enabled_buy:
        return OUT_OF_STOCK, f"button: {sold_out[0]['text']!r}"

    # 3) schema.org structured data.
    if jl:
        return jl, f"JSON-LD: {ev.jsonld_availability}"

    if ev.api_http_errors:
        return UNKNOWN, f"Target API refused (HTTP {sorted(set(ev.api_http_errors))}; likely bot-blocked)"
    return UNKNOWN, f"no clear signal (buttons={[b['text'] for b in ev.main_buttons][:5]})"


# JS run in the page: JSON-LD offers + buttons near the product title (h1),
# so "Add to cart" buttons in related-product carousels are ignored.
PAGE_JS = r"""
() => {
  const avail = [];
  let price = null;
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const walk = (o) => {
        if (!o || typeof o !== 'object') return;
        if (Array.isArray(o)) return o.forEach(walk);
        if (o.availability) avail.push(String(o.availability));
        if (o.price && !price) price = String(o.price);
        Object.values(o).forEach(walk);
      };
      walk(JSON.parse(s.textContent));
    } catch (e) {}
  }
  const h1 = document.querySelector('h1');
  let scope = h1, buttons = [];
  for (let i = 0; scope && i < 8; i++, scope = scope.parentElement) {
    const bs = [...scope.querySelectorAll('button, [role="button"]')]
      .map(b => ({text: (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 80),
                  disabled: b.disabled || b.getAttribute('aria-disabled') === 'true'}))
      .filter(b => b.text);
    if (bs.some(b => /add to cart|sold out|out of stock|unavailable|notify me|ship it|coming soon/i.test(b.text))) {
      buttons = bs; break;
    }
  }
  return {avail, price, buttons, head: (document.body ? document.body.innerText : '').slice(0, 3000)};
}
"""


# Field names that commonly carry real stock on Adobe Commerce (Magento) / headless storefronts.
STOCK_KEYS = (
    r"stock_status|stockStatus|is_in_stock|isInStock|is_salable|isSalable|salable_qty|salableQty|"
    r"only_x_left_in_stock|onlyXLeftInStock|qty|quantity|max_sale_qty|maxSaleQty|inventory|"
    r"availability|salesStatus|sales_status|purchasable|isPurchasable|orderable|isOrderable|"
    r"available|isAvailable|in_stock|inStock|soldOut|sold_out"
)
_STOCK_RE = re.compile(r'\\*"(' + STOCK_KEYS + r')\\*"\s*:\s*\\*"?([A-Za-z_0-9.\-]{1,40})')


def scan_stock_fields(text: str, source: str, limit: int = 40) -> list[tuple[str, str, str]]:
    """Find "key": value pairs for stock-like keys in JSON or in JSON embedded in HTML."""
    out, seen = [], set()
    for m in _STOCK_RE.finditer(text):
        item = (source, m.group(1), m.group(2))
        if item not in seen:
            seen.add(item)
            out.append(item)
            if len(out) >= limit:
                break
    return out


def collect(page, product: dict) -> Evidence:
    ev = Evidence()
    nintendo = product["retailer"] == "Nintendo Store"

    debug = os.environ.get("DEBUG") == "1"

    def on_response(resp):
        if "redsky" in resp.url and resp.status >= 400:
            ev.api_http_errors.append(resp.status)
        if debug and "redsky" in resp.url:
            body = ""
            try:
                body = resp.text()
            except Exception:
                pass
            print(f"  [debug] {resp.status} {resp.url[:160]}")
            for m in re.finditer(r'"(availability_status|is_out_of_stock_in_all_store_locations|'
                                 r'purchase_limit|availability)"\s*:\s*("?[A-Za-z_0-9]+"?)', body):
                print(f"      {m.group(1)}={m.group(2)}")
        if "redsky" in resp.url and "fulfillment" in resp.url:
            try:
                data = resp.json()
            except Exception:
                return
            found = re.findall(r'"availability_status"\s*:\s*"([A-Z_]+)"', json.dumps(data))
            ev.target_api_status.extend(found)

    def on_nintendo_response(resp):
        # Read-only: look at JSON the product page itself fetched (no extra requests made).
        try:
            if "nintendo" not in resp.url or "json" not in (resp.headers.get("content-type") or ""):
                return
            short = re.sub(r"\?.*", "", resp.url)[-80:]
            ev.stock_fields.extend(scan_stock_fields(resp.text(), f"api {short}"))
        except Exception:
            pass

    if product["retailer"] == "Target":
        page.on("response", on_response)
    if nintendo:
        page.on("response", on_nintendo_response)

    resp = page.goto(product["url"], wait_until="domcontentloaded", timeout=45_000)
    ev.http_status = resp.status if resp else None
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # pages with long-polling never go idle; continue with what loaded
    data = page.evaluate(PAGE_JS)
    ev.jsonld_availability = data["avail"]
    ev.main_buttons = data["buttons"]
    ev.page_text_head = data["head"]
    ev.price = data["price"]
    if nintendo:
        try:  # data embedded in the rendered page (e.g. __NEXT_DATA__ / hydration JSON)
            ev.stock_fields.extend(scan_stock_fields(page.content(), "page"))
        except Exception:
            pass
    if debug:
        print(page.evaluate(DEBUG_JS))
        if product["retailer"] == "Target" and resp:
            dump_served_html(resp.text(), product["tcin"])
    return ev


def dump_served_html(html: str, tcin: str) -> None:
    """Debug only: what stock data is embedded in the HTML Target's server returned?"""
    print(f"  [debug] served html: {len(html)} bytes, tcin mentions={html.count(tcin)}, "
          f"__TGT_DATA__={'__TGT_DATA__' in html}, __NEXT_DATA__={'__NEXT_DATA__' in html}")
    keys = (r"availability_status|is_out_of_stock_in_all_store_locations|"
            r"out_of_stock|sold_out|purchase_limit|shipping_options|availability")
    # Embedded JSON is often string-escaped (\"key\":\"VALUE\"), so allow optional backslashes.
    pat = re.compile(r'\\*"(' + keys + r')\\*"\s*:\s*\\*"?([A-Za-z_0-9]+)')
    for i, m in enumerate(pat.finditer(html)):
        if i >= 25:
            print("      ... (more)")
            break
        ctx = html[max(0, m.start() - 90):m.start()].replace("\\", "")
        print(f"      {m.group(1)}={m.group(2)}   <- ...{ctx[-90:]!r}")


# Debug dump: where we landed, the h1, and every element whose text looks like a buy/stock signal.
DEBUG_JS = r"""
() => {
  const out = [`  [debug] final=${location.href} title=${document.title.slice(0, 100)}`];
  const h1 = document.querySelector('h1');
  out.push(`  [debug] h1=${h1 ? h1.innerText.slice(0, 100) : null} ` +
           `jsonld=${document.querySelectorAll('script[type="application/ld+json"]').length}`);
  const re = /add to cart|ship it|pick it up|deliver it|sold out|out of stock|unavailable|notify me|coming soon|preorder/i;
  const seen = new Set();
  for (const el of document.querySelectorAll('button, [role="button"], [data-test], a, span, div, p')) {
    const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
    const t = (el.matches('button,[role="button"]') ? el.innerText : own).trim().replace(/\s+/g, ' ').slice(0, 80);
    if (!t || !re.test(t)) continue;
    const key = el.tagName + t + (el.getAttribute('data-test') || '');
    if (seen.has(key) || seen.size > 40) continue;
    seen.add(key);
    out.push(`  [debug] <${el.tagName.toLowerCase()} data-test=${el.getAttribute('data-test')} ` +
             `disabled=${!!el.disabled}> ${t}`);
  }
  out.push(`  [debug] body: ${(document.body ? document.body.innerText : '').replace(/\s+/g, ' ').slice(0, 600)}`);
  return out.join('\n');
}
"""


def notify(title: str, body: str, url: str, priority: str = "urgent", tag: str = "") -> None:
    """Send an ntfy push. `tag` (e.g. "in_stock") lets desktop_alert.py tell alert types apart."""
    topic = os.environ.get("NTFY_TOPIC")
    if os.environ.get("DRY_RUN") == "1" or not topic:
        prefix = "[TEST] " if os.environ.get("TEST_RUN") == "1" else ""
        print(f"[notify skipped] {prefix}{title}: {body} {url}")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    tags = ["rotating_light", tag]
    if os.environ.get("TEST_RUN") == "1":  # manual/test run: make it unmistakable
        title = "[TEST] " + title
        tags = ["test_tube"] + tags
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": ",".join(t for t in tags if t),
        "Click": url,
        # A visible button too: easier to hit from a lock screen or watch than the body.
        "Actions": f"view, Open product page, {url}, clear=true",
    }
    requests.post(f"{server}/{topic}", data=body.encode(), headers=headers, timeout=15).raise_for_status()


def check_once(ctx) -> list[tuple[dict, str, str]]:
    results = []
    for product in PRODUCTS:
        page = ctx.new_page()
        ev = None
        try:
            ev = collect(page, product)
            status, reason = decide(ev)
        except Exception as e:  # timeouts, navigation errors
            status, reason = UNKNOWN, f"error: {type(e).__name__}: {e}"[:200]
        finally:
            page.close()
        results.append((product, status, reason))
        print(f"{time.strftime('%H:%M:%S')} {product['retailer']:15} {status:12} {reason}", flush=True)
        # Log stock fields when the button says buyable (to diagnose "quantity not available"
        # false positives), or always on test runs.
        if ev and ev.stock_fields and (status == IN_STOCK or os.environ.get("DEBUG") == "1"):
            for source, key, value in ev.stock_fields:
                print(f"    stock field [{source}] {key}={value}", flush=True)
    return results


def send_alerts(results, state: dict, notify_unknown: bool) -> None:
    for prod, status, reason in results:
        prev = state.get(prod["retailer"], {})
        alert, state[prod["retailer"]] = transition(prev, status, notify_unknown)
        try:
            if alert == "in_stock":
                notify(f"Zelda Switch 2 IN STOCK at {prod['retailer']}", f"Tap to open. ({reason})",
                       prod["url"], tag="in_stock")
            elif alert == "sold_out":
                notify(f"Zelda Switch 2 sold out again at {prod['retailer']}", reason, prod["url"], priority="low")
            elif alert == "unknown":
                notify(f"Stock check couldn't read {prod['retailer']}", reason, prod["url"], priority="low")
        except Exception as e:  # a failed push must not stop the watch loop
            print(f"  push failed: {e}", flush=True)
        if alert:
            print(f"  alert={alert}", flush=True)


def main() -> int:
    """Check every CHECK_INTERVAL seconds until LOOP_MINUTES have passed (0 = check once).

    GitHub's */5 cron often fires 10-30+ min apart, which is longer than a typical drop.
    Looping inside the job, with the next scheduled run queued behind it, keeps coverage
    near-continuous no matter how late the schedule fires.
    """
    from playwright.sync_api import sync_playwright

    interval = max(30, int(os.environ.get("CHECK_INTERVAL", "60")))  # floor: be polite to the store
    deadline = time.time() + 60 * float(os.environ.get("LOOP_MINUTES", "0"))
    state_path = os.environ.get("STATE_FILE", "state.json")
    state = load_state(state_path)
    notify_unknown = os.environ.get("NOTIFY_ON_UNKNOWN") == "1"
    checks = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, locale="en-US", viewport={"width": 1366, "height": 900})
        while True:
            started = time.time()
            results = check_once(ctx)
            checks += 1
            send_alerts(results, state, notify_unknown)
            save_state(state_path, state)
            if started + interval >= deadline:
                break
            time.sleep(max(0, started + interval - time.time()))
        browser.close()

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"{checks} check(s); last result:\n\n| Retailer | Status | Reason |\n|---|---|---|\n")
            for prod, status, reason in results:
                f.write(f"| [{prod['retailer']}]({prod['url']}) | {status} | {reason} |\n")
    return 0


def load_state(path: str) -> dict:
    """Last statuses from the previous run (restored from the Actions cache). Missing = fresh start."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w") as f:
        json.dump(state, f)


def transition(prev: dict, status: str, notify_unknown: bool = False) -> tuple[str | None, dict]:
    """Push only on a status change, however often checks run; return (alert, new_state).

    `known` remembers the last non-UNKNOWN status, so a page that briefly fails to load
    mid-drop (IN_STOCK -> UNKNOWN -> IN_STOCK) doesn't trigger a second "IN STOCK" push.
    With no previous state, an IN_STOCK result still alerts (better a duplicate than a miss).
    """
    known_prev = prev.get("known")
    known = known_prev if status == UNKNOWN else status
    alert = None
    if status == IN_STOCK and known_prev != IN_STOCK:
        alert = "in_stock"
    elif status == OUT_OF_STOCK and known_prev == IN_STOCK:
        alert = "sold_out"
    elif status == UNKNOWN and prev.get("status") != UNKNOWN and notify_unknown:
        alert = "unknown"
    return alert, {"status": status, "known": known}


if __name__ == "__main__":
    sys.exit(main())
