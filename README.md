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

The repo must stay **public**: Actions minutes are free for public repos, while a private
repo would use ~13,000 min/month (the free tier is 2,000).

## What to expect

- **OUT_OF_STOCK** = working (reads the "Sold out" button next to the product title).
- **Checks every minute.** GitHub's `*/5` schedule often fires 10-30+ minutes apart, longer
  than a typical drop, so each scheduled run loops for 50 minutes checking every 60 s
  (`LOOP_MINUTES`, `CHECK_INTERVAL`). The next scheduled run waits in the queue and starts
  as soon as the current one ends, so coverage is near-continuous. Manual runs check once.
- **Pushes only on a status change**, however often it checks: one **urgent** push when it
  comes in stock, one low-priority "sold out again" push when it's gone, nothing in
  between. The last status is kept in the Actions cache (`state.json`); a one-off page
  failure mid-drop doesn't re-trigger the push. If the cache is lost, an in-stock result
  still pushes.
- The watcher reads the product page. Nintendo's page can show "Add to cart" while the
  cart still says "quantity not available" (seen Nov 16), so a push means "go try now",
  not a guaranteed purchase.
- GitHub disables scheduled workflows after 60 days with no repo commits.

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
