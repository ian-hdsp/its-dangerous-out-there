"""Check the Nintendo Store for the Zelda 40th Anniversary Switch 2 console.

Runs headless Chromium (Playwright), decides IN_STOCK / OUT_OF_STOCK / UNKNOWN per
retailer, and sends an urgent ntfy.sh push when anything is IN_STOCK.

Env vars:
  NTFY_TOPIC        required to send pushes (keep it secret; anyone with it can read them)
  NTFY_SERVER       optional, default https://ntfy.sh
  NOTIFY_ON_UNKNOWN optional "1" -> also push when a page can't be read (e.g. bot-blocked)
  DRY_RUN           optional "1" -> print instead of sending
  TEST_RUN          optional "1" -> label every push "[TEST]" with a test-tube tag (manual runs)
  PUSHOVER_USER / PUSHOVER_TOKEN  optional -> also send alerts via Pushover (IN STOCK = emergency)
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests

IN_STOCK, OUT_OF_STOCK, UNKNOWN = "IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"
# One reminder per drop, this many minutes after the IN STOCK push (drops last ~20 min). 0 = off.
REMINDER_SECS = 60 * float(os.environ.get("REMINDER_MINUTES", "3"))
# Checks land a fraction of a second either side of the interval; without slack, the check
# exactly 3 min later can measure 179.9 s and the reminder slips a whole interval.
REMINDER_SLACK_SECS = 5

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
    title: str = ""  # the page's h1 (product name)
    # Nintendo's live store data (graph.nintendo.com) for this product: True/False, or None if
    # not seen. The page can show "Add to cart" from a stale cache while this is False, and
    # then the cart says "The requested quantity is not available".
    salable: bool | None = None


def _jsonld_status(values: list[str]) -> str | None:
    vals = [v.rsplit("/", 1)[-1].lower() for v in values]
    if any(v in ("instock", "limitedavailability", "preorder", "onlineonly") for v in vals):
        return IN_STOCK
    if vals and all(v in ("outofstock", "soldout", "discontinued", "preordersoldout") for v in vals):
        return OUT_OF_STOCK
    return None


def decide(ev: Evidence) -> tuple[str, str]:
    """Return (status, reason). Conservative: only IN_STOCK on a positive signal.

    For the Nintendo Store, a buyable-looking page only counts as IN_STOCK if the store's
    live data also says the product is salable (isSalableQty). If that data says it isn't,
    the "Add to cart" button is a stale-cache false positive, so it's treated as sold out.
    """
    status, reason = _decide_page(ev)
    if status == IN_STOCK and ev.salable is False:
        return OUT_OF_STOCK, f"{reason}, but isSalableQty=false (cart would say quantity not available)"
    if status == IN_STOCK and ev.salable is True:
        return IN_STOCK, f"{reason} + isSalableQty=true"
    return status, reason


def _decide_page(ev: Evidence) -> tuple[str, str]:
    """Status from the page itself (HTTP status, block page, Target API, buttons, JSON-LD)."""
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
  return {avail, price, buttons, title: h1 ? h1.innerText.trim() : '',
          head: (document.body ? document.body.innerText : '').slice(0, 3000)};
}
"""


# Any field whose name mentions stock / salable / sold out / inventory / availability /
# purchasable / orderable / quantity (Adobe Commerce, Next.js payloads and JSON-LD all vary).
_STOCK_KEY = (r"[A-Za-z_]*(?:[Ss]tock|STOCK|[Ss]alable|[Ss]old_?[Oo]ut|[Ii]nventory|[Aa]vailab|"
              r"[Pp]urchas|[Oo]rderab|[Qq]uantity|[Qq]ty)[A-Za-z_]*")
_STOCK_RE = re.compile(r'\\*"(' + _STOCK_KEY + r')\\*"\s*:\s*\\*"?([^"\\,}\]]{1,80})')
# Third-party widgets on the page whose JSON isn't about the product (e.g. support chat).
_IGNORE_HOSTS = ("quiq-api.com", "quiq.com")


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


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().lower()


def find_salable(payloads: list, title: str) -> bool | None:
    """isSalableQty for the product whose name equals the page title, from live-API JSON.

    The same responses can include related products, so match by exact (normalised) name.
    Any False wins (never push on a conflict); None if this product isn't found.
    """
    want, found = _norm(title), []

    def walk(o):
        if isinstance(o, dict):
            if "isSalableQty" in o and want and _norm(str(o.get("name", ""))) == want:
                found.append(o["isSalableQty"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for p in payloads:
        walk(p)
    vals = [v for v in found if isinstance(v, bool)]
    if not vals:
        return None
    return all(vals)


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

    live_api = product.get("live_api", "graph.nintendo.com")
    live_payloads: list = []

    def on_nintendo_response(resp):
        # Read-only: look at JSON the product page itself fetched (no extra requests made).
        try:
            if live_api in resp.url:
                try:
                    live_payloads.append(resp.json())
                except Exception:
                    pass
            if ("nintendo" not in resp.url or any(h in resp.url for h in _IGNORE_HOSTS)
                    or "json" not in (resp.headers.get("content-type") or "")):
                return
            short = re.sub(r"\?.*", "", resp.url)[-80:]
            body = resp.text()
            ev.stock_fields.extend(scan_stock_fields(body, f"api {short}"))
            if os.environ.get("DEBUG") == "1" and "graph.nintendo.com" in resp.url:
                req = (resp.request.post_data or "")[:600]
                print(f"  [debug] graph request: {req!r}", flush=True)
                for m in re.finditer(r"isSalableQty", body):
                    print(f"  [debug] graph ...{body[max(0, m.start() - 700):m.start() + 120]!r}", flush=True)
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
    ev.title = data.get("title", "")
    if nintendo:
        ev.salable = find_salable(live_payloads, ev.title)
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


PUSHOVER_API = "https://api.pushover.net/1"


def pushover_users() -> list[str]:
    """PUSHOVER_USER may hold several User Keys, comma-separated (you, a friend, ...)."""
    return [u.strip() for u in os.environ.get("PUSHOVER_USER", "").split(",") if u.strip()]


def notify(title: str, body: str, url: str, priority: str = "urgent", tag: str = "",
           channels: tuple[str, ...] = ("ntfy", "pushover")) -> list[str]:
    """Send one alert to every configured channel: ntfy (NTFY_TOPIC) and Pushover
    (PUSHOVER_USER + PUSHOVER_TOKEN; one message per person). Channels and people are
    independent: one failing never stops the rest, and nothing here raises. `tag` (e.g.
    "in_stock") lets desktop_alert.py tell alert types apart. Returns the Pushover emergency
    receipts, one per person, so repeats can be cancelled when the drop ends. `channels`
    limits which services get this alert (e.g. the same-drop reminder is ntfy only)."""
    test = os.environ.get("TEST_RUN") == "1"
    if test:  # manual/test run: make it unmistakable on every channel
        title = "[TEST] " + title
    topic = os.environ.get("NTFY_TOPIC")
    po_users, po_token = pushover_users(), os.environ.get("PUSHOVER_TOKEN")
    if os.environ.get("DRY_RUN") == "1" or not (topic or (po_users and po_token)):
        print(f"[notify skipped] {title}: {body} {url}")
        return []
    receipts: list[str] = []
    if topic and "ntfy" in channels:
        try:
            _notify_ntfy(topic, title, body, url, priority, tag, test)
        except Exception as e:
            print(f"  ntfy push failed: {e}", flush=True)
    if po_token and "pushover" in channels:
        # A separate message per person, so each acknowledges their own emergency alert
        # (with one shared message, the first person to acknowledge stops it for everyone).
        for i, user in enumerate(po_users, 1):
            try:
                receipt = _notify_pushover(user, po_token, title, body, url, priority, tag, test)
                if receipt:
                    receipts.append(receipt)
            except Exception as e:  # keys are secrets: report which person, never the key
                print(f"  Pushover push to person #{i} failed: {e}", flush=True)
    return receipts


def _notify_ntfy(topic, title, body, url, priority, tag, test) -> None:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    tags = (["test_tube"] if test else []) + ["rotating_light", tag]
    headers = {
        "Title": title,
        "Priority": priority,
        "Tags": ",".join(t for t in tags if t),
        "Click": url,
        # A visible button too: easier to hit from a lock screen or watch than the body.
        "Actions": f"view, Open product page, {url}, clear=true",
    }
    requests.post(f"{server}/{topic}", data=body.encode(), headers=headers, timeout=15).raise_for_status()


def pushover_priority(priority: str, tag: str) -> int:
    """IN STOCK = 2 (emergency: repeats until acknowledged; Critical Alert on iPhone if enabled),
    other urgent (reminder) = 1 (high), low (sold out) = -1 (quiet), anything else = 0."""
    if tag == "in_stock":
        return 2
    return {"urgent": 1, "high": 1, "low": -1, "min": -2}.get(priority, 0)


def _notify_pushover(user, token, title, body, url, priority, tag, test) -> str | None:
    prio = pushover_priority(priority, tag)
    data = {"token": token, "user": user, "title": title[:250], "message": body[:1024],
            "url": url, "url_title": "Open product page", "priority": prio}
    if prio == 2:
        # Repeat every PUSHOVER_RETRY_SECONDS (default 180 = 3 min) until acknowledged, stopping
        # after PUSHOVER_EXPIRE_SECONDS (default 360 = 6 min: the real stock goes fast, so a late
        # alert isn't useful); tests stop after 3 min. Pushover: retry >= 30 s, expire <= 10800 s.
        retry = max(30, int(os.environ.get("PUSHOVER_RETRY_SECONDS") or 180))
        expire = min(10800, max(retry, int(os.environ.get("PUSHOVER_EXPIRE_SECONDS") or 360)))
        data.update(retry=retry, expire=180 if test else expire)
    r = requests.post(f"{PUSHOVER_API}/messages.json", data=data, timeout=15)
    r.raise_for_status()
    return r.json().get("receipt") if prio == 2 else None


def cancel_pushover(receipts) -> None:
    """Stop emergency alerts repeating (e.g. the drop sold out before they were acknowledged).
    Takes a list of receipts, or a single one (state saved by older versions)."""
    token = os.environ.get("PUSHOVER_TOKEN")
    if isinstance(receipts, str):
        receipts = [receipts]
    if not (receipts and token) or os.environ.get("DRY_RUN") == "1":
        return
    for receipt in receipts:
        try:
            requests.post(f"{PUSHOVER_API}/receipts/{receipt}/cancel.json", data={"token": token},
                          timeout=15).raise_for_status()
        except Exception as e:
            print(f"  Pushover cancel failed: {e}", flush=True)


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
        live = f"  [live isSalableQty={ev.salable}]" if ev is not None and product["retailer"] == "Nintendo Store" else ""
        print(f"{time.strftime('%H:%M:%S')} {product['retailer']:15} {status:12} {reason}{live}", flush=True)
        # Log stock fields when the button says buyable (to diagnose "quantity not available"
        # false positives), or always on test runs.
        if ev and ev.stock_fields and (status == IN_STOCK or os.environ.get("DEBUG") == "1"):
            for source, key, value in ev.stock_fields:
                print(f"    stock field [{source}] {key}={value}", flush=True)
    return results


DROP_LOG_FIELDS = ["retailer", "start_after_utc", "start_by_utc", "end_after_utc", "end_by_utc",
                   "min_minutes", "max_minutes", "start_pst", "start_est"]


def _iso(ts: float | None) -> str:
    if ts is None:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def drop_row(retailer: str, entry: dict, end_by: float) -> dict:
    """One finished drop. Checks run every ~minute, so each edge is known to a window:
    start_after = last sold-out check before it, start_by = first in-stock check (the push);
    end_after = last in-stock check, end_by = first sold-out check (the sold-out push).
    min/max_minutes bound how long it was really in stock. Unknown edges stay blank."""
    start_after, start_by = entry.get("drop_start_after"), entry.get("since")
    end_after = entry.get("last_in_at")
    mins = lambda a, b: f"{(b - a) / 60:.1f}" if a is not None and b is not None else ""
    pst, est = (drop_started_label(start_by).split(" / ") if start_by is not None else ("", ""))
    return {
        "retailer": retailer,
        "start_after_utc": _iso(start_after), "start_by_utc": _iso(start_by),
        "end_after_utc": _iso(end_after), "end_by_utc": _iso(end_by),
        "min_minutes": mins(start_by, end_after), "max_minutes": mins(start_after, end_by),
        "start_pst": pst, "start_est": est,
    }


def append_drop_log(path: str, row: dict) -> None:
    import csv
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=DROP_LOG_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def send_alerts(results, state: dict, notify_unknown: bool) -> None:
    for prod, status, reason in results:
        prev = state.get(prod["retailer"], {})
        now = time.time()
        alert, state[prod["retailer"]] = transition(prev, status, notify_unknown, now, REMINDER_SECS)
        entry = state[prod["retailer"]]
        # Drop log bookkeeping (live runs set DROP_LOG; test runs never write it).
        if alert == "in_stock":
            entry["drop_start_after"] = prev.get("last_out_at")
        if alert == "sold_out" and os.environ.get("DROP_LOG"):
            try:
                row = drop_row(prod["retailer"], prev, now)  # prev: the drop's state before sell-out
                append_drop_log(os.environ["DROP_LOG"], row)
                print(f"  drop logged: {row}", flush=True)
            except Exception as e:  # logging must never stop the watch loop
                print(f"  drop log failed: {e}", flush=True)
        if status == IN_STOCK:
            entry["last_in_at"] = now
        elif status == OUT_OF_STOCK:
            entry["last_out_at"] = now
            entry.pop("last_in_at", None)
            entry.pop("drop_start_after", None)
        try:
            if alert == "in_stock":
                receipts = notify(f"Zelda Switch 2 IN STOCK at {prod['retailer']}", f"Tap to open. ({reason})",
                                  prod["url"], tag="in_stock")
                if receipts:
                    entry["pushover_receipts"] = receipts  # one per person, to cancel on sell-out
            elif alert == "reminder":
                # Same drop, not a new one: says so in the title, with when the drop started.
                # No "in_stock" tag, so desktop_alert.py doesn't reopen the browser.
                since = state[prod["retailer"]]["since"]
                mins = round((now - since) / 60)
                notify(f"Reminder (same drop): Zelda Switch 2 still in stock at {prod['retailer']}"
                       f" - started {drop_started_label(since)}",
                       f"Not a new drop. In stock for {mins} min so far. Tap to open.",
                       prod["url"], tag="repeat",
                       channels=("ntfy",))  # Pushover's emergency repeats already cover this
            elif alert == "sold_out":
                # stop repeats for anyone who hasn't acknowledged (old state used a single key)
                cancel_pushover(entry.pop("pushover_receipts", None) or entry.pop("pushover_receipt", None))
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

    if os.environ.get("SEND_TEST_PUSH") == "1":
        # One push in the real IN STOCK format, to check GitHub -> ntfy -> phone end to end.
        # Only ever on test runs, so it's always titled "[TEST] ..." with the test-tube tag.
        os.environ["TEST_RUN"] = "1"
        prod = PRODUCTS[0]
        notify(f"Zelda Switch 2 IN STOCK at {prod['retailer']}",
               "Tap to open. (end-to-end test push: not a real drop)", prod["url"], tag="in_stock")
        # Pushover emergency test alerts repeat every 60 s for up to 3 min unless acknowledged.
        print("sent [TEST] push", flush=True)

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


def transition(prev: dict, status: str, notify_unknown: bool = False,
               now: float = 0.0, reminder_secs: float = 0.0) -> tuple[str | None, dict]:
    """Push on a status change, plus one reminder per drop; return (alert, new_state).

    `known` remembers the last non-UNKNOWN status, so a page that briefly fails to load
    mid-drop (IN_STOCK -> UNKNOWN -> IN_STOCK) doesn't trigger a second "IN STOCK" push.
    `since` is when the current drop was first seen; once `reminder_secs` have passed while
    still in stock, a single "reminder" goes out (`reminded` stops a second one). Both reset
    when it sells out. reminder_secs <= 0 turns reminders off.
    With no previous state, an IN_STOCK result still alerts (better a duplicate than a miss).
    """
    known_prev = prev.get("known")
    known = known_prev if status == UNKNOWN else status
    since, reminded = prev.get("since"), prev.get("reminded", False)
    alert = None
    if status == IN_STOCK and known_prev != IN_STOCK:
        alert = "in_stock"
        since, reminded = now, False
    elif (status == IN_STOCK and reminder_secs > 0 and not reminded
          and since is not None and now - since >= reminder_secs - REMINDER_SLACK_SECS):
        alert = "reminder"
        reminded = True
    elif status == OUT_OF_STOCK and known_prev == IN_STOCK:
        alert = "sold_out"
    elif status == UNKNOWN and prev.get("status") != UNKNOWN and notify_unknown:
        alert = "unknown"
    if status == OUT_OF_STOCK:
        since, reminded = None, False
    return alert, {**prev, "status": status, "known": known, "since": since, "reminded": reminded}


def drop_started_label(since: float) -> str:
    """'11:16 AM PST / 2:16 PM EST' for when the drop was first seen (handles PDT/EDT too)."""
    utc = datetime.datetime.fromtimestamp(since, datetime.timezone.utc)

    def fmt(zone: str) -> str:
        local = utc.astimezone(ZoneInfo(zone))
        return f"{local.hour % 12 or 12}:{local:%M %p %Z}"

    return f"{fmt('America/Los_Angeles')} / {fmt('America/New_York')}"


if __name__ == "__main__":
    sys.exit(main())
