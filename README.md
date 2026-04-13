# Tradetron – Kotak Neo V3 Token Regeneration

Automates the daily broker token regeneration on [Tradetron](https://tradetron.tech). Logs in with your credentials, visits the regenerate-token URL, and confirms success — no manual clicks needed.

Runs automatically at **8:45 AM IST every weekday** via GitHub Actions + cron-job.org.

---

## How it works

1. Opens Chrome (headless) and navigates to `https://tradetron.tech/login`
2. Fills in email + password, solves the ALTCHA captcha automatically
3. Visits your `REGEN_TOKEN_URL` (unique to your broker account)
4. Tradetron regenerates the broker token and redirects to the dashboard

---

## Setup Guide

### Step 1 — Fork this repo

Click **Fork** (top right on GitHub) → **Create fork**

You now have your own copy at:
`https://github.com/YOUR_USERNAME/tt-login-automation`

---

### Step 2 — Add GitHub Secrets

Go to your forked repo:
**Settings → Secrets and variables → Actions → New repository secret**

Add these 3 secrets:

| Secret name          | Value |
|----------------------|-------|
| `TRADETRON_EMAIL`    | your Tradetron login email |
| `TRADETRON_PASSWORD` | your Tradetron password |
| `REGEN_TOKEN_URL`    | your broker's regenerate-token URL |

**How to find your `REGEN_TOKEN_URL`:**
1. Log into [tradetron.tech](https://tradetron.tech)
2. Go to **Brokers & Exchanges**
3. Click **Renew** next to your broker
4. Copy the URL from the browser address bar — it looks like:
   `https://tradetron.tech/user/broker-and-exchanges/regenerate-token/123`
   The number at the end is unique to your account.

---

### Step 3 — Enable GitHub Actions

Go to the **Actions** tab in your repo and click **"I understand my workflows, go ahead and enable them"** if prompted.

Test it manually:
- Go to **Actions → Kotak Neo V3 Token Regeneration**
- Click **Run workflow → Run workflow**
- Wait ~30 seconds — you should see a green ✅ and `Token generated successfully`

---

### Step 4 — Create a GitHub Token

> Required so cron-job.org can trigger your workflow automatically.

1. Go to [github.com/settings/tokens/new](https://github.com/settings/tokens/new)
2. Fill in:
   - Note: `tt-login-automation trigger`
   - Expiration: `No expiration`
   - Scope: tick only **workflow**
3. Click **Generate token** → copy and save it safely (you won't see it again)

---

### Step 5 — Set up cron-job.org

> GitHub's built-in scheduler is unreliable on free accounts. cron-job.org triggers it reliably at the exact time.

1. Sign up free at [cron-job.org](https://cron-job.org)
2. Click **CREATE CRONJOB**
3. Fill in:
   - **Title:** `Tradetron Kotak Token`
   - **URL:** `https://api.github.com/repos/YOUR_USERNAME/tt-login-automation/actions/workflows/kotak-token.yml/dispatches`
     *(replace `YOUR_USERNAME` with your GitHub username)*
   - **Method:** `POST`
   - **Request body:** `{"ref":"main"}`
4. Under **Schedule:**
   - Timezone: `Asia/Calcutta`
   - Time: `8:45 AM`
   - Days: tick **Mon, Tue, Wed, Thu, Fri** only
5. Under **Headers** add 2 entries:
   - `Authorization` → `Bearer YOUR_GITHUB_TOKEN`
   - `Content-Type` → `application/json`
6. Click **Save** → **Enable**

Done! cron-job.org will trigger your GitHub Action every weekday at 8:45 AM IST automatically.

---

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp env.example .env
# Edit .env with your credentials
```

```bash
python kotakneo_autologin.py           # headless (weekdays only)
python kotakneo_autologin.py --force   # bypass weekday check (for testing)
python kotakneo_autologin.py --headed  # show the browser window
```

---

## Configuration

Edit `.env` (copy from `env.example`):

```env
TRADETRON_EMAIL=you@example.com
TRADETRON_PASSWORD=yourpassword
REGEN_TOKEN_URL=https://tradetron.tech/user/broker-and-exchanges/regenerate-token/YOUR_BROKER_ID
```

Optional:

```env
CHROME_BINARY=/path/to/chrome   # only needed if Chrome is not in the default location
```

---

---

## Error-Execution Auto-Retry

A second script (`tradetron_error_retry.py`) scans your deployed strategies every 10 minutes during market hours. If any strategy is in **Error-Execution** state, it automatically clicks **Manage → Proceed (Try Again)** and sends you a **Telegram notification**.

### How it works

1. Runs every 10 min between **9:00 AM – 3:30 PM IST** on weekdays
2. Logs into Tradetron and checks the deployed strategies page
3. Finds any strategy with `Error-Execution` status
4. Clicks **Manage** → **Proceed**
5. Sends a Telegram message: `⚠️ Strategy XYZ was retried at 10:32 AM IST`

### Telegram Setup

**Step 1 – Create a bot:**
1. Open Telegram → search **@BotFather**
2. Send `/newbot` → choose a name → copy the **token**

**Step 2 – Get your Chat ID:**
1. Message **@userinfobot** on Telegram
2. It replies with your **Chat ID** number

**Step 3 – Add as GitHub Secrets:**

| Secret name          | Value |
|----------------------|-------|
| `TELEGRAM_BOT_TOKEN` | token from BotFather |
| `TELEGRAM_CHAT_ID`   | your chat ID number |

### Run locally

```bash
python tradetron_error_retry.py           # market hours only
python tradetron_error_retry.py --force   # bypass market hours (for testing)
python tradetron_error_retry.py --headed  # show browser window
```

### Schedule via cron-job.org

Same as the token renewal — create a second cronjob on [cron-job.org](https://cron-job.org) that triggers the `error-retry.yml` workflow every 10 minutes between 9 AM – 3:30 PM IST on weekdays.

URL:
```
https://api.github.com/repos/YOUR_USERNAME/tt-login-automation/actions/workflows/error-retry.yml/dispatches
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside your venv |
| `SessionNotCreatedException` | `webdriver-manager` auto-downloads the right ChromeDriver — ensure it's in `requirements.txt` |
| Login fails | Run `--headed` locally to watch the browser and check for UI changes |
| Token not regenerated | Verify `REGEN_TOKEN_URL` is set correctly — find it by clicking **Renew** on the Brokers & Exchanges page |
| Weekend skip message | Use `--force` flag to bypass the weekday check during testing |
| Scheduled job not running | GitHub's free-tier scheduler is unreliable — use cron-job.org (Step 5) to trigger it reliably |

---

## Security

- `.env` is git-ignored — never commit it
- On GitHub, credentials are stored as encrypted Secrets
- The repo can be kept private — GitHub Actions still runs free (2000 min/month)
- Your GitHub token in cron-job.org only has `workflow` scope — it cannot access code or data
