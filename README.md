# Tradetron – Kotak Neo V3 Token Regeneration

Automates the daily Kotak Neo V3 broker token regeneration on [Tradetron](https://tradetron.tech). Logs in with your credentials, visits the regenerate-token URL, and confirms success — no manual clicks needed.

Runs automatically at **8:30 AM IST every weekday** via GitHub Actions (free).

---

## How it works

1. Opens Chrome (headless) and navigates to `https://tradetron.tech/login`
2. Fills in email + password, solves the ALTCHA captcha automatically
3. Visits `https://tradetron.tech/user/broker-and-exchanges/regenerate-token/917`
4. Tradetron regenerates the Kotak Neo V3 token and redirects to the dashboard

---

## Run on your own GitHub account

### 1. Fork or clone this repo

```bash
git clone https://github.com/AnilMalapati/tt-login-automation.git
cd tt-login-automation
```

Or click **Fork** on GitHub to copy it to your account.

### 2. Add your credentials as GitHub Secrets

Go to your repo on GitHub:  
**Settings → Secrets and variables → Actions → New repository secret**

Add these two secrets:

| Secret name          | Value                        |
|----------------------|------------------------------|
| `TRADETRON_EMAIL`    | your Tradetron login email   |
| `TRADETRON_PASSWORD` | your Tradetron password      |
| `REGEN_TOKEN_URL`    | your broker's regenerate-token URL (see below) |

> Your credentials are encrypted and never exposed in logs.

**How to find your `REGEN_TOKEN_URL`:**
1. Log into [tradetron.tech](https://tradetron.tech)
2. Go to **Brokers & Exchanges**
3. Click the **Renew** button next to your broker
4. Copy the URL from your browser's address bar — it looks like:  
   `https://tradetron.tech/user/broker-and-exchanges/regenerate-token/917`  
   The number at the end is unique to your account.

### 3. Enable GitHub Actions

Go to the **Actions** tab in your repo and click **"I understand my workflows, go ahead and enable them"** if prompted.

### 4. Run a test

- Go to **Actions → Kotak Neo V3 Token Regeneration**
- Click **Run workflow → Run workflow**
- Watch the logs — you should see `Token generated successfully` at the end

### 5. Automatic daily runs

The workflow runs automatically at **3:00 AM UTC (8:30 AM IST)** on weekdays.  
No further action needed.

---

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp env.example .env
# Edit .env — add your TRADETRON_EMAIL and TRADETRON_PASSWORD
```

```bash
python kotakneo_autologin.py          # runs only on weekdays (IST)
python kotakneo_autologin.py --force  # bypass weekday check (for testing)
python kotakneo_autologin.py --headed # show the browser window
```

---

## Configuration

Edit `.env` (copy from `env.example`):

```env
TRADETRON_EMAIL=you@example.com
TRADETRON_PASSWORD=yourpassword
```

Optional overrides (defaults work for most users):

```env
TRADETRON_LOGIN_URL=https://tradetron.tech/login
REGEN_TOKEN_URL=https://tradetron.tech/user/broker-and-exchanges/regenerate-token/917
CHROME_BINARY=/path/to/chrome   # only needed if Chrome isn't in the default location
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` inside your venv |
| `SessionNotCreatedException` | `webdriver-manager` auto-downloads the right ChromeDriver — ensure it's in `requirements.txt` |
| Login fails | Run `--headed` locally to watch the browser and check for UI changes |
| Token not regenerated | Verify the `REGEN_TOKEN_URL` ends with your broker's correct ID (917 for Kotak Neo V3) |
| Weekend skip message | Use `--force` flag to bypass the weekday check during testing |

---

## Security

- `.env` is git-ignored — never commit it
- On GitHub, credentials are stored as encrypted Secrets
- The repo can be kept private — GitHub Actions still runs for free (2000 min/month)
