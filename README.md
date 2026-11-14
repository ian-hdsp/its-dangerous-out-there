# Zelda Switch 2 stock watch

GitHub Actions checks the real Target and Nintendo Store product pages every ~5 minutes
in headless Chrome and sends an urgent phone push (via ntfy) when the
Zelda 40th Anniversary Switch 2 console can be added to the cart.

## Setup (about 5 minutes)

1. **Phone:** install the free **ntfy** app (iOS / Android). Tap **+**, subscribe to a
   hard-to-guess topic name, e.g. `zelda-ian-7f3k9q2x`. Anyone who knows the name can read it.
   Set the notification to make sound / break through Focus if you want.
2. **GitHub:** create a new **public** repo and upload these files (keep the folder layout,
   including `.github/workflows/`). Public matters: Actions minutes are free for public
   repos; a private repo would use ~13,000 min/month, well over the 2,000 free.
   Nothing secret is in the code — the topic lives in a secret.
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   `NTFY_TOPIC` = your topic name.
   Optional variable `NOTIFY_ON_UNKNOWN` = `1` to get a low-priority push when a page
   can't be read (useful for the first day, noisy after).
4. Repo → **Actions** tab → enable workflows → **Zelda Switch 2 stock check → Run workflow**.
   Open the run: the summary shows each retailer as IN_STOCK / OUT_OF_STOCK / UNKNOWN.

## Runs on a home computer (self-hosted runner)

Target refuses GitHub's data-center IPs (its API returns HTTP 435), so the workflow uses
`runs-on: self-hosted`: a GitHub runner on a home computer with a residential IP.

1. **Lock down fork PRs first (important: this repo is public).** Settings → Actions →
   General → "Approval for running fork pull request workflows from contributors" →
   **Require approval for all external contributors**. Without this, a stranger's pull
   request could run code on your computer.
2. Settings → Actions → **Runners → New self-hosted runner**, pick your OS, and run the
   Download and Configure commands it shows (accept the defaults).
3. Keep it running: `./run.sh` (or `run.cmd` on Windows) in a terminal, or install it as a
   service so it survives restarts: `sudo ./svc.sh install && sudo ./svc.sh start`
   (macOS/Linux) or choose "run as service" during config on Windows.
4. Needs Python available on that computer. On Linux, also run once:
   `python -m playwright install-deps chromium` (needs sudo).

While the computer is asleep or off, nothing is checked (runs wait in the queue).

## What to expect

- **OUT_OF_STOCK** for both = working.
- **UNKNOWN "Target API refused (HTTP 435)"** = Target still sees a bot, even from home.
- GitHub's `*/5` schedule is best-effort; runs can be 5–15+ min late at busy times.
- GitHub disables scheduled workflows after 60 days with no repo commits.
- When it fires, it pushes every run (every ~5 min) while the item stays in stock.

## Test locally

```
pip install -r requirements.txt pytest && python -m playwright install chromium
DRY_RUN=1 python check_stock.py
PYTHONPATH=. pytest -q tests
```
