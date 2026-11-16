# Zelda Switch 2 stock watch

GitHub Actions checks the Nintendo Store product page every ~5 minutes in headless
Chrome and sends an urgent phone push (via ntfy) when the Zelda 40th Anniversary
Switch 2 console can be added to the cart.

Product: <https://www.nintendo.com/us/store/products/nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition-121642/>

## Setup

1. **Phone:** install the free **ntfy** app (iOS / Android). Tap **+** and subscribe to a
   hard-to-guess topic name. Anyone who knows the name can read the pushes.
   Set the notification to make sound / break through Focus if you want.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   `NTFY_TOPIC` = your topic name.
   Optional variable `NOTIFY_ON_UNKNOWN` = `1` to get a low-priority push when the page
   can't be read.
3. Repo → **Actions** → **Zelda Switch 2 stock check → Run workflow**. The run summary
   shows IN_STOCK / OUT_OF_STOCK / UNKNOWN. Manual runs also print debug detail in the log.
   **Manual runs are test runs:** any push they send is titled `[TEST] …` with a 🧪 tag,
   they keep their own saved status (so they never affect real alerts), and
   `desktop_alert.py` ignores them. Set `loop_minutes` to test the every-minute loop.
   To cover a gap in the schedule for real, run it with `mode: live` and `loop_minutes: 50`:
   that behaves exactly like a scheduled run (real pushes, shared status).

The repo must stay **public**: Actions minutes are free for public repos, while a private
repo would use ~13,000 min/month (the free tier is 2,000).

## What to expect

- **OUT_OF_STOCK** = working (reads the "Sold out" button next to the product title).
- **Checks every minute.** GitHub's `*/5` schedule often fires 10-30+ minutes apart, longer
  than a typical drop, so each scheduled run loops for 50 minutes checking every 60 s
  (`LOOP_MINUTES`, `CHECK_INTERVAL`). The next scheduled run waits in the queue and starts
  as soon as the current one ends, so coverage is near-continuous. Manual runs check once.
- **Pushes on a status change, plus one reminder per drop:** one **urgent** push when it
  comes in stock; if it's still in stock 3 minutes later (`REMINDER_MINUTES`), one urgent
  push titled **"Reminder (same drop): … still in stock — started 11:16 AM PST / 2:16 PM
  EST"** with "Not a new drop" in the body (no `in_stock` tag, so `desktop_alert.py`
  doesn't reopen the browser); then one low-priority "sold out again" push when it's gone.
  Drops on Nov 16 showed ~20 minutes each, but real stock seemed to go in the first minutes, so the reminder comes early. The last status
  is kept in the Actions cache (`state.json`); a one-off page failure mid-drop neither
  re-triggers the push nor resets the reminder clock. If the cache is lost, an in-stock
  result still pushes.
- **False-positive filter:** Nintendo's page can show "Add to cart" from a stale cache while
  the cart says "The requested quantity is not available" (seen Nov 16). The page also loads
  the store's live data from `graph.nintendo.com`, which includes `isSalableQty` for the
  product. The watcher only pushes IN STOCK when the button says "Add to cart" **and**
  `isSalableQty=true` for this product (matched by its exact title, since related products
  appear in the same data). If the live data isn't seen at all, it falls back to the button
  alone rather than risk missing a drop. Each check's log line shows the live value.
- GitHub disables scheduled workflows after 60 days with no repo commits.

## Pushover (optional second channel, recommended on iPhone)

Alerts also go to **Pushover** when the repo secrets `PUSHOVER_USER` and `PUSHOVER_TOKEN`
are set. ntfy and Pushover are independent: if one fails, the other still sends.

| Alert | Pushover priority |
|---|---|
| IN STOCK | **Emergency (2)**: repeats every 3 min (repo variable `PUSHOVER_RETRY_SECONDS`, min 30) until you tap *Acknowledge*, for up to 6 min (`PUSHOVER_EXPIRE_SECONDS`); stops automatically if it sells out first |
| Reminder (same drop) | High (1): one alert |
| Sold out again | Quiet (-1) |

**More than one person (e.g. a friend):** put several User Keys in `PUSHOVER_USER`,
comma-separated (`yourKey,friendKey`). Each person gets their **own** alert and acknowledges
it themselves (with one shared message, the first to acknowledge would stop it for everyone);
one bad key never blocks the others, and selling out stops everyone's repeats. Each person
needs their own Pushover account (and license after the trial). For ntfy, a friend just
subscribes to the same topic.

Setup: install Pushover, turn on **Settings → Critical Alerts** in the app (emergency alerts
then sound even on silent / in Focus), copy **Your User Key** from pushover.net, create an
application there for an **API Token**, and add both as repo secrets. Test it with a manual
run (`mode: test`, `send_test_push: true`): a `[TEST]` emergency alert that repeats for up to
3 minutes unless acknowledged.

## Check out faster

The watcher never buys anything; these just get you to "Add to cart" sooner.

- **Phone:** the IN STOCK push has an **Open product page** button (easier from a lock
  screen or watch), and tapping the push itself also opens the page.
- **Computer:** `desktop_alert.py` listens for the push and, within a second or two, opens
  the product page in your default browser (where you're already signed in) and plays a
  sound. Standard-library Python only, nothing to install:

  ```
  python desktop_alert.py YOUR_NTFY_TOPIC --test   # opens the page once: check it works
  python desktop_alert.py YOUR_NTFY_TOPIC          # leave running
  ```

  It ignores sold-out pushes, never opens the same push twice, and skips IN STOCK pushes
  older than 10 minutes (e.g. after the computer wakes from sleep). Don't commit your
  topic name: this repo is public.
- **Before the next drop:** stay signed in to the Nintendo Store on phone and computer;
  save your shipping address and a payment method in your Nintendo Account; make sure
  2-step verification codes reach you fast; and do a practice checkout of a cheap in-stock
  item up to the final "Place order" step.

## Why not Target?

Target's bot protection refuses GitHub-hosted runners: the product page loads, but the
stock API it calls returns HTTP 435, so Target could only ever show UNKNOWN here. Target
is covered by a separate hourly restock-tracker alert instead. The Target parsing code is
still in `check_stock.py` (see the comment above `PRODUCTS`) if a runner with a
residential IP is ever added.

## Test locally

```
pip install -r requirements.txt pytest && python -m playwright install chromium
DRY_RUN=1 python check_stock.py
PYTHONPATH=. pytest -q tests
```

### Drop simulation (end to end, ~4 min)

`tools/simulate_drop.py` runs the real check loop in headless Chromium against a local mock
of the product page that switches from "Sold out" to "Add to cart" and back, with a mock
ntfy server timing each push. It never touches the real store or your real topic. It
passes if there is exactly one urgent IN STOCK push and one "sold out again" push, each
within one check interval (+ check time) of the page change.

```
python tools/simulate_drop.py                            # current settings (60 s checks)
python tools/simulate_drop.py --interval 30 --minutes 2.5   # quicker
```

With the current settings the IN STOCK push typically arrives ~30 s after the page changes
(worst case ~66 s). For the Nov 16 drop, the old every-5-minutes schedule pushed ~5 min
after the page changed (up to 10 min); its average across 100 runs was ~8.5 min.
