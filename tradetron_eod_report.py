#!/usr/bin/env python3
"""
Tradetron – End-of-Day Error-Execution Report

Logs in, scans every page of the deployed strategies list, and sends ONE
Telegram message listing every strategy that is in "Error-Execution" state
at that moment. Read-only: it never clicks Manage / Proceed (that is the job
of tradetron_error_retry.py).

A message is sent every run, including "all healthy", so a missing message
means the report itself failed, not that everything was fine.

Usage:
  python tradetron_eod_report.py            # weekdays only
  python tradetron_eod_report.py --force    # bypass weekday check
  python tradetron_eod_report.py --headed   # visible browser (debug)
"""

import argparse
import datetime
import os
import sys
import time

import pytz
from dotenv import load_dotenv
from selenium.webdriver.support.ui import WebDriverWait

from tradetron_error_retry import (
    DEPLOYED_URL,
    _find_error_manage_links,
    _goto_page,
    _strategy_name_near_manage_link,
    build_driver,
    send_telegram,
)
from tt_login import log, tradetron_login

_HERE = os.path.dirname(os.path.abspath(__file__))
IST = pytz.timezone("Asia/Kolkata")
MAX_PAGES = 20


def is_weekday_ist():
    return datetime.datetime.now(IST).weekday() < 5


def collect_error_strategies(driver):
    """Walk every page of the deployed strategies list and return
    (names, pages_scanned). `names` is an ordered, de-duplicated list of the
    strategies currently in Error-Execution."""
    driver.get(DEPLOYED_URL)
    time.sleep(4)

    names = []
    seen = set()
    pages_scanned = 0
    for page in range(1, MAX_PAGES + 1):
        pages_scanned = page
        for link in _find_error_manage_links(driver):
            name = _strategy_name_near_manage_link(driver, link) or "Unknown Strategy"
            if name not in seen:
                seen.add(name)
                names.append(name)
                log(f"   Error-Execution on page {page}: {name}")
        if not _goto_page(driver, page + 1):
            break
    return names, pages_scanned


def build_message(names, pages_scanned, now_str):
    if names:
        parts = [f"🚨 <b>Tradetron EOD report: {len(names)} strategy(ies) in Error-Execution</b>", ""]
        parts.extend(f"• {n}" for n in names)
    else:
        parts = ["✅ <b>Tradetron EOD report: no strategies in Error-Execution</b>"]
    parts.append(f"\n🕐 {now_str} · scanned {pages_scanned} page(s)")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--force", action="store_true", help="Bypass weekday check")
    args = parser.parse_args()

    load_dotenv(os.path.join(_HERE, ".env"))

    if not args.force and not is_weekday_ist():
        log("Weekend (IST) — skipping. Use --force to override.")
        sys.exit(0)

    email = os.getenv("TRADETRON_EMAIL", "").strip()
    password = os.getenv("TRADETRON_PASSWORD", "").strip()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not email or not password:
        log("ERROR: Set TRADETRON_EMAIL and TRADETRON_PASSWORD in .env")
        sys.exit(1)

    log("Starting end-of-day Error-Execution report...")
    driver = build_driver(headless=not args.headed)
    wait = WebDriverWait(driver, 30)

    exit_code = 0
    try:
        tradetron_login(driver, wait, email, password)
        names, pages_scanned = collect_error_strategies(driver)

        now_str = datetime.datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")
        msg = build_message(names, pages_scanned, now_str)
        log(msg.replace("<b>", "").replace("</b>", ""))

        if telegram_token and telegram_chat:
            send_telegram(telegram_token, telegram_chat, msg)
        else:
            log("   (No Telegram credentials set — skipping notification)")
            exit_code = 1
    except Exception as e:
        log(f"✖ Error: {e}")
        try:
            driver.save_screenshot(os.path.join(_HERE, "eod_debug.png"))
        except Exception:
            pass
        if telegram_token and telegram_chat:
            send_telegram(
                telegram_token, telegram_chat,
                f"❌ <b>Tradetron EOD report failed</b>\n<code>{str(e)[:300]}</code>",
            )
        exit_code = 1
    finally:
        try:
            driver.save_screenshot(os.path.join(_HERE, "eod_last_run.png"))
        except Exception:
            pass
        driver.quit()
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
