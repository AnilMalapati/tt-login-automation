#!/usr/bin/env python3
"""
Tradetron – Kotak Neo V3 daily token regeneration.

Flow:
  1. Log into tradetron.tech with email + password
  2. Navigate to the regenerate-token URL — that's it

Usage:
  python kotakneo_autologin.py            # headless (server / cron)
  python kotakneo_autologin.py --headed   # with visible browser (debug)
  python kotakneo_autologin.py --force    # run even on weekends (testing)
"""

import argparse
import datetime
import os
import platform
import sys
import time

import pytz
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains

_HERE = os.path.dirname(os.path.abspath(__file__))

LOGIN_URL          = "https://tradetron.tech/login"


def log(msg):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)


def is_weekday_ist():
    return datetime.datetime.now(pytz.timezone("Asia/Kolkata")).weekday() < 5


def build_driver(headless=True):
    opts = Options()
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1440,900")
    opts.add_argument("--disable-extensions")
    if headless:
        opts.add_argument("--headless=new")

    if platform.system() == "Darwin":
        opts.binary_location = (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
    else:
        cb = os.getenv("CHROME_BINARY", "/opt/google/chrome/chrome")
        if os.path.isfile(cb):
            opts.binary_location = cb

    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)


def run(headless=True):
    load_dotenv(os.path.join(_HERE, ".env"))

    email          = os.getenv("TRADETRON_EMAIL", "").strip()
    password       = os.getenv("TRADETRON_PASSWORD", "").strip()
    regen_token_url = os.getenv("REGEN_TOKEN_URL", "").strip()

    if not email or not password:
        log("ERROR: Set TRADETRON_EMAIL and TRADETRON_PASSWORD in .env")
        return False

    if not regen_token_url:
        log("ERROR: Set REGEN_TOKEN_URL in .env  (e.g. https://tradetron.tech/user/broker-and-exchanges/regenerate-token/917)")
        return False

    driver = build_driver(headless)
    wait   = WebDriverWait(driver, 30)

    try:
        # Block NextRoll/AdRoll cookie popup before any page loads
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
            "*nextroll.com*", "*adroll.com*", "*nr-data.net*",
            "*d.adroll.com*", "*s.adroll.com*"
        ]})
        log("   NextRoll popup blocked via CDP.")

        # Patch attachShadow so closed shadow roots become open (accessible via JS)
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
            const _attachShadow = Element.prototype.attachShadow;
            Element.prototype.attachShadow = function(init) {
                return _attachShadow.call(this, { ...init, mode: 'open' });
            };
        """})
        log("   Shadow DOM patch applied.")

        # ── Step 1: Login ──────────────────────────────────────────────────────
        log("Logging into Tradetron...")
        driver.get(LOGIN_URL)

        email_field = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='email']")
        ))
        email_field.clear()
        email_field.send_keys(email)

        pwd_field = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='password']")
        ))
        pwd_field.clear()
        pwd_field.send_keys(password)

        # Cookie popup is handled at driver level (NextRoll blocked via CDP)

        # ── ALTCHA captcha — light DOM widget with verify() API ───────────────
        log("   Handling ALTCHA captcha...")
        try:
            # Wait up to 8s for the widget to appear
            altcha_triggered = False
            for attempt in range(8):
                result = driver.execute_script("""
                    var widget = document.querySelector('altcha-widget');
                    if (!widget) return 'no-widget';

                    // Prefer the verify() API (triggers proof-of-work directly)
                    if (typeof widget.verify === 'function') {
                        try { widget.verify(); return 'verify-called'; } catch(e) {}
                    }

                    // Fallback: click checkbox in light DOM (no shadow root)
                    var root = widget.shadowRoot || widget;
                    var cb = root.querySelector('input[type="checkbox"]');
                    if (!cb) return 'no-checkbox';
                    cb.click();
                    return 'clicked';
                """)
                log(f"   ALTCHA attempt {attempt+1}: {result}")
                if result in ("clicked", "verify-called"):
                    altcha_triggered = True
                    break
                if result == "no-widget":
                    time.sleep(1)
                    continue
                time.sleep(1)

            if not altcha_triggered:
                # Last resort: direct Selenium click on the checkbox
                cbs = driver.find_elements(By.CSS_SELECTOR, "altcha-widget input[type='checkbox']")
                if cbs:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cbs[0])
                    time.sleep(0.3)
                    cbs[0].click()
                    log("   Selenium-clicked ALTCHA checkbox.")
                    altcha_triggered = True
                else:
                    log("   ALTCHA checkbox not found — proceeding anyway.")

            if altcha_triggered:
                log("   Waiting for ALTCHA proof-of-work to complete (up to 30s)...")
                for _ in range(30):
                    time.sleep(1)
                    state = driver.execute_script("""
                        var w = document.querySelector('altcha-widget');
                        if (!w) return 'no-widget';
                        var d = w.querySelector('[data-state]');
                        return d ? d.getAttribute('data-state') : (w.getAttribute('state') || 'pending');
                    """)
                    log(f"   ALTCHA state: {state}")
                    if state == "verified":
                        log("   ✔ ALTCHA verified.")
                        break
        except Exception as e:
            log(f"   ALTCHA step error: {e} — continuing anyway...")

        time.sleep(0.5)
        before_url = driver.current_url
        wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "button[type='submit']")
        )).click()

        # Wait for redirect away from login
        WebDriverWait(driver, 20).until(lambda d: d.current_url != before_url)
        time.sleep(2)
        log(f"✔ Logged in — {driver.current_url}")

        # ── Step 2: Hit the regenerate-token URL ───────────────────────────────
        log(f"Opening: {regen_token_url}")
        driver.get(regen_token_url)
        time.sleep(3)

        log(f"✔ Done — final URL: {driver.current_url}")

        # Save a screenshot to confirm
        path = os.path.join(_HERE, "last_run.png")
        driver.save_screenshot(path)
        log(f"   Screenshot → {path}")

        return True

    except Exception as e:
        log(f"✖ Error: {e}")
        try:
            driver.save_screenshot(os.path.join(_HERE, "debug.png"))
            log(f"   Screenshot → {os.path.join(_HERE, 'debug.png')}")
        except Exception:
            pass
        return False

    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Tradetron Kotak Neo V3 token regeneration")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    parser.add_argument("--force",  action="store_true", help="Run even on weekends (for testing)")
    args = parser.parse_args()

    load_dotenv(os.path.join(_HERE, ".env"))
    if not args.force and not is_weekday_ist():
        log("Weekend (IST) — skipping. Use --force to run anyway.")
        sys.exit(0)

    log("Starting Kotak Neo V3 token regeneration...")
    sys.exit(0 if run(headless=not args.headed) else 1)


if __name__ == "__main__":
    main()
