#!/usr/bin/env python3
"""
Tradetron – Error-Execution Strategy Auto-Retry

Scans the deployed strategies page for any strategy in "Error-Execution" state,
clicks Manage → Proceed (Try Again), and sends a Telegram notification.

Usage:
  python tradetron_error_retry.py            # headless, IST market hours only
  python tradetron_error_retry.py --headed   # visible browser (debug)
  python tradetron_error_retry.py --force    # bypass market hours check
"""

import argparse
import datetime
import os
import sys
import time

import pytz
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

_HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOYED_URL = "https://tradetron.tech/deployed-strategies"
LOGIN_URL    = "https://tradetron.tech/login"
IST          = pytz.timezone("Asia/Kolkata")


def log(msg):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)


def is_market_hours_ist():
    now = datetime.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def send_telegram(token, chat_id, message):
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=10)
        if resp.status_code == 200:
            log("   Telegram notification sent.")
        else:
            log(f"   Telegram error: {resp.text}")
    except Exception as e:
        log(f"   Telegram send failed: {e}")


def build_driver(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-gpu")

    import platform
    if platform.system() == "Darwin":
        chrome_bin = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    else:
        chrome_bin = os.getenv("CHROME_BINARY", "/opt/google/chrome/chrome")
    if os.path.exists(chrome_bin):
        opts.binary_location = chrome_bin

    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)


def tradetron_login(driver, wait, email, password):
    log("Logging into Tradetron...")
    driver.get(LOGIN_URL)

    # Block NextRoll cookie popup
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
        "*nextroll.com*", "*adroll.com*", "*nr-data.net*"
    ]})
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
        const _orig = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function(init) {
            return _orig.call(this, { ...init, mode: 'open' });
        };
    """})
    driver.get(LOGIN_URL)
    time.sleep(3)

    # Fill email
    email_field = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='email'], input[name='email']")
    ))
    email_field.clear()
    email_field.send_keys(email)

    # Fill password
    pwd_field = wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "input[type='password']")
    ))
    pwd_field.clear()
    pwd_field.send_keys(password)

    # Handle ALTCHA captcha
    log("   Handling ALTCHA captcha...")
    for attempt in range(8):
        result = driver.execute_script("""
            var widget = document.querySelector('altcha-widget');
            if (!widget) return 'no-widget';
            if (typeof widget.verify === 'function') {
                try { widget.verify(); return 'verify-called'; } catch(e) {}
            }
            var root = widget.shadowRoot || widget;
            var cb = root.querySelector('input[type="checkbox"]');
            if (!cb) return 'no-checkbox';
            cb.click();
            return 'clicked';
        """)
        log(f"   ALTCHA attempt {attempt+1}: {result}")
        if result in ("clicked", "verify-called"):
            break
        time.sleep(1)

    # Wait for ALTCHA to verify
    for _ in range(30):
        time.sleep(1)
        state = driver.execute_script("""
            var w = document.querySelector('altcha-widget');
            if (!w) return 'no-widget';
            var d = w.querySelector('[data-state]');
            return d ? d.getAttribute('data-state') : (w.getAttribute('state') || 'pending');
        """)
        if state == "verified":
            log("   ✔ ALTCHA verified.")
            break

    # Submit
    time.sleep(0.5)
    before_url = driver.current_url
    wait.until(EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "button[type='submit']")
    )).click()

    WebDriverWait(driver, 20).until(lambda d: d.current_url != before_url)
    time.sleep(2)
    log(f"✔ Logged in — {driver.current_url}")


def retry_error_strategies(driver, wait):
    log(f"Scanning deployed strategies for errors...")
    driver.get(DEPLOYED_URL)
    time.sleep(4)

    retried = []

    while True:
        # Find all "Error-Execution" Manage links on current page
        error_manages = driver.find_elements(
            By.XPATH,
            "//*[contains(text(),'Error-Execution')]/following-sibling::*[contains(text(),'Manage')] | "
            "//*[contains(text(),'Error-Execution')]/..//*[contains(text(),'Manage')]"
        )

        if not error_manages:
            # Also try finding by status container
            error_manages = driver.find_elements(
                By.XPATH,
                "//span[contains(@class,'status') and contains(text(),'Error')]"
                "/following::a[contains(text(),'Manage')][1] | "
                "//*[contains(text(),'Error-Execution')]/following::*[normalize-space(text())='Manage'][1]"
            )

        if not error_manages:
            log("   No Error-Execution strategies found.")
            break

        log(f"   Found {len(error_manages)} strategy/strategies in Error-Execution state.")

        # Get strategy name before clicking Manage
        try:
            strategy_card = error_manages[0].find_element(
                By.XPATH, "./ancestor::div[contains(@class,'strategy') or contains(@class,'card')][1]"
            )
            strategy_name = strategy_card.find_element(By.XPATH, ".//*[contains(@class,'name') or contains(@class,'title')][1]").text
        except Exception:
            strategy_name = "Unknown Strategy"

        log(f"   Retrying: {strategy_name}")

        # Click Manage
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", error_manages[0])
        time.sleep(0.5)
        error_manages[0].click()
        time.sleep(2)

        # Wait for Manage Positions modal
        try:
            proceed_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[normalize-space(text())='Proceed']")
                )
            )

            # Verify all action dropdowns are set to "Try Again" (they default to it)
            dropdowns = driver.find_elements(
                By.XPATH, "//select[ancestor::*[contains(@class,'modal') or contains(@class,'dialog')]]"
            )
            for dropdown in dropdowns:
                options = dropdown.find_elements(By.TAG_NAME, "option")
                for opt in options:
                    if "try" in opt.text.lower():
                        driver.execute_script(
                            "arguments[0].value = arguments[1];", dropdown, opt.get_attribute("value")
                        )
                        break

            # Click Proceed
            proceed_btn.click()
            log(f"   ✔ Clicked Proceed for: {strategy_name}")
            retried.append(strategy_name)
            time.sleep(3)

        except Exception as e:
            log(f"   Could not click Proceed: {e}")
            # Close modal if open
            try:
                close_btn = driver.find_element(By.XPATH, "//button[@aria-label='Close' or contains(@class,'close')]")
                close_btn.click()
            except Exception:
                pass
            time.sleep(2)
            break

        # Refresh to check for more errors
        driver.get(DEPLOYED_URL)
        time.sleep(4)

    return retried


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--force",  action="store_true", help="Bypass market hours check")
    args = parser.parse_args()

    load_dotenv(os.path.join(_HERE, ".env"))

    if not args.force and not is_market_hours_ist():
        now = datetime.datetime.now(IST)
        log(f"Outside market hours ({now.strftime('%H:%M IST')}) — skipping. Use --force to override.")
        sys.exit(0)

    email          = os.getenv("TRADETRON_EMAIL", "").strip()
    password       = os.getenv("TRADETRON_PASSWORD", "").strip()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not email or not password:
        log("ERROR: Set TRADETRON_EMAIL and TRADETRON_PASSWORD in .env")
        sys.exit(1)

    log("Starting Error-Execution retry scan...")
    driver = build_driver(headless=not args.headed)
    wait   = WebDriverWait(driver, 30)

    try:
        tradetron_login(driver, wait, email, password)
        retried = retry_error_strategies(driver, wait)

        if retried:
            now_str = datetime.datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")
            msg = (
                f"⚠️ <b>Tradetron Error-Execution Retried</b>\n\n"
                + "\n".join(f"• {s}" for s in retried)
                + f"\n\n🕐 {now_str}"
            )
            log(f"Retried {len(retried)} strategy/strategies: {retried}")

            if telegram_token and telegram_chat:
                send_telegram(telegram_token, telegram_chat, msg)
            else:
                log("   (No Telegram credentials set — skipping notification)")
        else:
            log("✔ All strategies healthy — nothing to retry.")

        driver.save_screenshot(os.path.join(_HERE, "retry_last_run.png"))

    except Exception as e:
        log(f"✖ Error: {e}")
        try:
            driver.save_screenshot(os.path.join(_HERE, "retry_debug.png"))
        except Exception:
            pass
        sys.exit(1)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
