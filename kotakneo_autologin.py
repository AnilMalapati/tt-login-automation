#!/usr/bin/env python3
"""
Tradetron – Kotak Neo V3 daily token regeneration.

Flow:
  1. Log into tradetron.tech with email + password
  2. Navigate to the regenerate-token URL twice (second pass after redirect)

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
from selenium.webdriver.support.ui import WebDriverWait

from tt_login import dismiss_overlays, log, tradetron_login

_HERE = os.path.dirname(os.path.abspath(__file__))


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


def page_looks_like_token_success(driver):
    text = (driver.page_source or "").lower()
    needles = (
        "token generated successfully",
        "token regenerated",
        "token generated",
        "successfully generated",
    )
    return any(n in text for n in needles)


def run(headless=True):
    load_dotenv(os.path.join(_HERE, ".env"))

    email = os.getenv("TRADETRON_EMAIL", "").strip()
    password = os.getenv("TRADETRON_PASSWORD", "").strip()
    regen_token_url = os.getenv("REGEN_TOKEN_URL", "").strip()

    if not email or not password:
        log("ERROR: Set TRADETRON_EMAIL and TRADETRON_PASSWORD in .env")
        return False

    if not regen_token_url:
        log("ERROR: Set REGEN_TOKEN_URL in .env  (e.g. https://tradetron.tech/user/broker-and-exchanges/regenerate-token/917)")
        return False

    driver = build_driver(headless)
    wait = WebDriverWait(driver, 30)

    try:
        tradetron_login(driver, wait, email, password)

        # Tradetron regenerates on visit and redirects. A second pass after that
        # redirect makes the renewal more reliable.
        saw_success = False
        for attempt in range(1, 3):
            log(f"Opening regenerate-token URL (attempt {attempt}/2): {regen_token_url}")
            driver.get(regen_token_url)
            time.sleep(3)
            dismiss_overlays(driver)
            log(f"   After attempt {attempt}: {driver.current_url}")
            if page_looks_like_token_success(driver):
                saw_success = True
                log("   ✔ Success message detected on page.")

        if not saw_success:
            # Direct regenerate URLs often just redirect with a flash toast.
            # Landing back on brokers/dashboard after a logged-in visit is OK.
            url = (driver.current_url or "").lower()
            if "login" in url:
                log("✖ Still on login after regenerate — token likely not renewed.")
                path = os.path.join(_HERE, "debug.png")
                driver.save_screenshot(path)
                log(f"   Screenshot → {path}")
                return False
            log("   No explicit success toast found; continuing (redirect after regen is normal).")

        log(f"✔ Done — final URL: {driver.current_url}")
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
    parser.add_argument("--force", action="store_true", help="Run even on weekends (for testing)")
    args = parser.parse_args()

    load_dotenv(os.path.join(_HERE, ".env"))
    if not args.force and not is_weekday_ist():
        log("Weekend (IST) — skipping. Use --force to run anyway.")
        sys.exit(0)

    log("Starting Kotak Neo V3 token regeneration...")
    sys.exit(0 if run(headless=not args.headed) else 1)


if __name__ == "__main__":
    main()
