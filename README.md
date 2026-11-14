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

The repo must stay **public**: Actions minutes are free for public repos, while a private
repo would use ~13,000 min/month (the free tier is 2,000).

## What to expect

- **OUT_OF_STOCK** = working (reads the "Sold out" button next to the product title).
- Pushes: one **urgent** push when it comes in stock, then a high-priority "still in
  stock" reminder on every run (~5 min) while it lasts, and one low-priority "sold out
  again" push when it's gone. Nothing while it stays sold out. The last status is kept in
  the Actions cache (`state.json`); a one-off page failure mid-drop doesn't re-trigger the
  urgent push. If the cache is ever lost, an in-stock result still pushes.
- GitHub's `*/5` schedule is best-effort; runs can be 5–15+ min late at busy times.
- GitHub disables scheduled workflows after 60 days with no repo commits.

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
