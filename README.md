# Tradetron broker auto sign-in

Automates opening Tradetron, logging in, going to **Brokers & integrations**, and clicking **Auto sign in** for your broker (Tradetron).

## API vs Selenium

[Tradetron’s documented API](https://help.tradetron.tech/en/article/tradetron-api-d79vly/) is for **strategy signals** (`api.tradetron.tech` with a strategy auth token). It does **not** replace the daily broker session refresh in the web app. This project uses **Selenium** for that UI flow.

## Setup

```bash
cd tradetron_autologin
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp env.example .env
# Edit .env with your email, password, broker name or a custom XPath
```

Test with a visible browser (useful if the broker opens an OAuth popup):

```bash
python autologin.py --headed
```

Headless (typical for a server/cron):

```bash
python autologin.py
```

## Configuration

- **`TRADETRON_BROKER_NAME`** + **`TRADETRON_AUTO_SIGNIN_BUTTON_TEXT`**: finds a row containing the broker name, then a button/link with that label (case-insensitive).
- **`TRADETRON_AUTO_SIGNIN_XPATH`**: if the layout is fussy, set an XPath from DevTools (often most reliable).
- **`TRADETRON_BROKERS_URL`**: defaults to `https://tradetron.tech/user/broker-and-exchanges` — change if your account uses another path.
- Optional **IST time window** vars in `env.example` restrict when the script runs (skip outside the window).

## Cron (IST)

Example: 8:35 AM India time on weekdays (ensure server TZ or use explicit `TZ=Asia/Kolkata`):

```cron
35 8 * * 1-5 cd /path/to/tradetron_autologin && . .venv/bin/activate && TZ=Asia/Kolkata python autologin.py >> /var/log/tradetron_autologin.log 2>&1
```

## Security

Keep `.env` private (see `.gitignore`). Prefer a dedicated Tradetron password and 2FA policy that matches how you automate (some flows need `--headed` once or app-specific tokens).

## Troubleshooting

- **Selectors**: If login or the button fails, run `--headed`, open DevTools, copy selectors into `TRADETRON_*_CSS` or `TRADETRON_AUTO_SIGNIN_XPATH`.
- **Chrome / ChromeDriver**: Requires a recent Selenium (4.6+) so the built-in manager can resolve ChromeDriver. On Linux, install Chrome and set `CHROME_BINARY` in `.env` if it is not at `/opt/google/chrome/chrome`.
