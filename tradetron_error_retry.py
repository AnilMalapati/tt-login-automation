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
import html
import os
import sys
import time

import pytz
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoAlertPresentException,
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from tt_login import log, tradetron_login

_HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOYED_URL = "https://tradetron.tech/deployed-strategies"
IST          = pytz.timezone("Asia/Kolkata")


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


def _safe_click(driver, el):
    """Normal click first; if the element is hidden/covered/not interactable,
    fall back to a JS click instead of crashing the whole run."""
    try:
        el.click()
    except ElementNotInteractableException:
        driver.execute_script("arguments[0].click();", el)
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", el)


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


def _drain_alert(driver):
    """If a browser alert is present, accept it and return its text. Else None."""
    try:
        alert = driver.switch_to.alert
        text = alert.text
        alert.accept()
        return text
    except NoAlertPresentException:
        return None


_NAME_BLOCKLIST = {
    "manage positions", "manage", "proceed", "error-execution",
    "date", "time", "condition", "instrument", "instrument symbol",
    "qty", "pending quantity", "actions", "price", "amount",
    "status", "execution",
    "details", "detail", "view", "view details", "view positions", "positions",
    "orders", "trades", "edit", "delete", "more", "info", "close", "try again",
}

# Trailing/leading arrows and bullets that link text often carries ("Details ▸").
_JUNK_CHARS = "▸▶►›»>→•·|:- \u00a0\t\r\n"


def _clean_name(text):
    """Trim UI glyphs (▸ › » …) from link text and collapse whitespace."""
    if not text:
        return ""
    return " ".join(text.replace("\u00a0", " ").split()).strip(_JUNK_CHARS)


def _looks_like_strategy_name(text):
    if not text:
        return False
    t = text.strip()
    if len(t) < 4 or len(t) > 200:
        return False
    if t.lower() in _NAME_BLOCKLIST:
        return False
    if t.replace(".", "").replace(",", "").replace(" ", "").isdigit():
        return False
    return True


def _strategy_name_near_manage_link(driver, manage_link):
    """Walk up the DOM from the Manage link looking for the strategy title in the
    same row/card. Link text is cleaned of UI glyphs ("Details ▸" -> "Details")
    and rejected if it is a generic UI label, so only a real title qualifies."""
    name = driver.execute_script(
        r"""
        const blocklist = new Set(arguments[1]);
        const clean = (s) => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ')
            .replace(/^[\s▸▶►›»>→•·|:-]+|[\s▸▶►›»>→•·|:-]+$/g, '');
        const looksLikeName = (s) => {
            const t = clean(s);
            if (t.length < 4 || t.length > 200) return false;
            if (blocklist.has(t.toLowerCase())) return false;
            if (/^[\d.,\s%₹+-]+$/.test(t)) return false;   // numbers / amounts
            return true;
        };
        let el = arguments[0];
        const manageCount = (root) => [...root.querySelectorAll('a,button,span')]
            .filter(n => (n.textContent || '').trim() === 'Manage').length;
        for (let depth = 0; depth < 12 && el; depth++) {
            // Stop once the container spans more than one row: anything found
            // above that point could belong to a different strategy.
            if (manageCount(el) > 1) break;
            // 1) Headings / title-like elements first: the real title is usually here
            for (const h of el.querySelectorAll('h1,h2,h3,h4,h5,h6,.strategy-name,.strategy_name,[class*="strategy-title"],[class*="strategyName"],[class*="strategy_name"]')) {
                const text = clean(h.textContent);
                if (looksLikeName(text)) return text;
            }
            // 2) Then anchors that look like strategy links (and are not UI labels)
            for (const a of el.querySelectorAll('a')) {
                const href = (a.getAttribute('href') || '').toLowerCase();
                const text = clean(a.textContent);
                if ((href.includes('strategy') || href.includes('deployed')) && looksLikeName(text)) {
                    return text;
                }
            }
            el = el.parentElement;
        }
        return null;
        """,
        manage_link,
        list(_NAME_BLOCKLIST),
    )
    return _clean_name(name) or None


def _debug_row_context(driver, manage_link):
    """Log what the row looks like when no name could be found, so the next run's
    log shows the real DOM instead of guessing again."""
    try:
        info = driver.execute_script(
            """
            let el = arguments[0];
            for (let i = 0; i < 6 && el.parentElement; i++) el = el.parentElement;
            const links = [...el.querySelectorAll('a')].slice(0, 12)
                .map(a => (a.textContent || '').trim().slice(0, 40) + ' -> ' + (a.getAttribute('href') || ''));
            return {text: (el.innerText || '').slice(0, 400), links: links};
            """,
            manage_link,
        )
        log(f"   (name not found) row text: {info.get('text')!r}")
        for l in info.get("links", []):
            log(f"      link: {l}")
    except Exception as e:
        log(f"   (couldn't read row context: {e})")


def _strategy_name_from_modal(driver):
    """Fallback: read the strategy title from inside the Manage Positions modal.
    The title is usually a centered row above the columns table; in DOM it can
    be a th[colspan], a div with no class, or just inline text — so we walk
    every visible text node in the modal and pick the first plausible one."""
    return driver.execute_script(
        r"""
        const blocklist = new Set(arguments[0]);
        const clean = (s) => (s || '').replace(/\u00a0/g, ' ').replace(/\s+/g, ' ')
            .replace(/^[\s▸▶►›»>→•·|:-]+|[\s▸▶►›»>→•·|:-]+$/g, '');
        const looksLikeName = (s) => {
            const t = clean(s);
            if (t.length < 4 || t.length > 200) return false;
            if (blocklist.has(t.toLowerCase())) return false;
            if (/^[\d.,\s%₹+-]+$/.test(t)) return false;
            return true;
        };
        const modal = document.querySelector('.modal, .modal-dialog, [role="dialog"]');
        if (!modal) return null;
        const walker = document.createTreeWalker(modal, NodeFilter.SHOW_TEXT);
        let node;
        while ((node = walker.nextNode())) {
            const text = clean(node.nodeValue);
            if (looksLikeName(text)) return text;
        }
        return null;
        """,
        list(_NAME_BLOCKLIST),
    )


def _close_modal(driver):
    for xp in (
        "//button[@aria-label='Close']",
        "//*[contains(@class,'modal') or contains(@class,'dialog')]//button[contains(@class,'close')]",
        "//*[contains(@class,'modal') or contains(@class,'dialog')]//*[normalize-space(text())='×' or normalize-space(text())='X']",
    ):
        try:
            _safe_click(driver, driver.find_element(By.XPATH, xp))
            return True
        except Exception:
            continue
    return False


def _find_error_manage_links(driver):
    error_manages = driver.find_elements(
        By.XPATH,
        "//*[contains(text(),'Error-Execution')]/following-sibling::*[contains(text(),'Manage')] | "
        "//*[contains(text(),'Error-Execution')]/..//*[contains(text(),'Manage')]"
    )
    if not error_manages:
        error_manages = driver.find_elements(
            By.XPATH,
            "//span[contains(@class,'status') and contains(text(),'Error')]"
            "/following::a[contains(text(),'Manage')][1] | "
            "//*[contains(text(),'Error-Execution')]/following::*[normalize-space(text())='Manage'][1]"
        )
    return error_manages


def _goto_page(driver, page_num):
    """Click the pagination control for `page_num`. Returns True on success."""
    try:
        btn = driver.find_element(
            By.XPATH,
            f"//*[(self::a or self::button or self::li)][normalize-space(text())='{page_num}']",
        )
    except Exception:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.3)
        btn.click()
        time.sleep(4)
        return True
    except Exception:
        return False


def _find_first_error_link_across_pages(driver, skip_names=None, max_pages=10):
    """Start at page 1 of deployed strategies, scan each page in order, and
    return the first (manage_link, strategy_name) whose name is NOT in
    `skip_names`. Returns (None, None) if no eligible link is found.

    Passing `skip_names` lets the caller dedupe strategies that have already
    been attempted in this run, so a flapping strategy doesn't block others
    behind it in the queue."""
    skip_names = skip_names or set()
    driver.get(DEPLOYED_URL)
    time.sleep(4)

    for page in range(1, max_pages + 1):
        for link in _find_error_manage_links(driver):
            name = _strategy_name_near_manage_link(driver, link)
            if not name:
                _debug_row_context(driver, link)
            name_key = name or "Unknown Strategy"
            if name_key in skip_names:
                continue
            log(f"   Found Error-Execution on page {page}: {name or '(unnamed)'}")
            return link, name
        if not _goto_page(driver, page + 1):
            return None, None
    return None, None


def retry_error_strategies(driver, wait):
    """Scan deployed strategies and click Manage → Proceed for each one in
    Error-Execution. Returns (retried, failed) where failed is a list of
    (name, reason) tuples."""
    log("Scanning deployed strategies for errors (all pages)...")

    retried = []
    failed = []
    attempted = set()  # strategies we've already tried this run (success or fail)
    MAX_ITER = 25

    for _ in range(MAX_ITER):
        manage_link, pre_click_name = _find_first_error_link_across_pages(
            driver, skip_names=attempted
        )
        if manage_link is None:
            if attempted:
                log("   No more Error-Execution strategies to retry this run.")
            else:
                log("   No Error-Execution strategies found.")
            break

        # Reserve this strategy in `attempted` BEFORE we touch it — so even if
        # the click/modal/retry path raises, we won't pick the same one again
        # this run. The next cron tick will revisit anything still in error.
        name_for_skip = pre_click_name or "Unknown Strategy"
        attempted.add(name_for_skip)

        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", manage_link)
        time.sleep(0.5)

        try:
            _safe_click(driver, manage_link)
        except UnexpectedAlertPresentException:
            alert_text = _drain_alert(driver) or "(no text)"
            log(f"   ✖ Alert on Manage click for '{name_for_skip}': {alert_text}")
            failed.append((name_for_skip, alert_text))
            if "market is closed" in alert_text.lower():
                log("   Market is closed — stopping further retries.")
                break
            continue

        try:
            proceed_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//button[normalize-space(text())='Proceed']")
                )
            )
        except TimeoutException:
            log(f"   ✖ Manage Positions modal did not appear for '{name_for_skip}'.")
            failed.append((name_for_skip, "modal did not open"))
            _close_modal(driver)
            time.sleep(1)
            continue

        modal_name = _strategy_name_from_modal(driver)
        strategy_name = pre_click_name or modal_name or "Unknown Strategy"
        if strategy_name != name_for_skip:
            attempted.add(strategy_name)  # keep both keys to be safe
        log(f"   Retrying: {strategy_name}")

        # Force every action dropdown to a "Try Again" option (defaults usually do).
        try:
            dropdowns = driver.find_elements(
                By.XPATH,
                "//select[ancestor::*[contains(@class,'modal') or contains(@class,'dialog')]]"
            )
            for dropdown in dropdowns:
                for opt in dropdown.find_elements(By.TAG_NAME, "option"):
                    if "try" in opt.text.lower():
                        driver.execute_script(
                            "arguments[0].value = arguments[1];"
                            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                            dropdown, opt.get_attribute("value"),
                        )
                        break
        except Exception as e:
            log(f"   (Couldn't normalize action dropdowns: {e})")

        try:
            _safe_click(driver, proceed_btn)
        except UnexpectedAlertPresentException:
            pass

        # Give the server a moment to respond — it may surface a JS alert
        # (e.g. "market is closed now") instead of accepting the retry.
        time.sleep(2)
        alert_text = _drain_alert(driver)
        if alert_text:
            log(f"   ✖ Server rejected retry for '{strategy_name}': {alert_text}")
            failed.append((strategy_name, alert_text))
            _close_modal(driver)
            # If market is closed, every subsequent retry would hit the same
            # alert — stop early instead of burning iterations.
            if "market is closed" in alert_text.lower():
                log("   Market is closed — stopping further retries.")
                break
            time.sleep(1)
            continue

        log(f"   ✔ Proceed accepted for: {strategy_name}")
        retried.append(strategy_name)
        time.sleep(2)

    return retried, failed


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

    exit_code = 0
    try:
        tradetron_login(driver, wait, email, password)
        retried, failed = retry_error_strategies(driver, wait)

        now_str = datetime.datetime.now(IST).strftime("%d %b %Y %I:%M %p IST")

        if retried or failed:
            log(f"Retried: {retried}")
            if failed:
                log(f"Failed:  {failed}")

            parts = ["⚠️ <b>Tradetron Error-Execution Report</b>"]
            if retried:
                parts.append("\n<b>Retried successfully:</b>")
                parts.extend(f"• {html.escape(s)}" for s in retried)
            if failed:
                parts.append("\n<b>Retry failed:</b>")
                parts.extend(f"• {html.escape(name)} — {html.escape(str(reason))}" for name, reason in failed)
            parts.append(f"\n🕐 {now_str}")
            msg = "\n".join(parts)

            if telegram_token and telegram_chat:
                send_telegram(telegram_token, telegram_chat, msg)
            else:
                log("   (No Telegram credentials set — skipping notification)")

            # Non-zero exit so CI surfaces the issue when retries didn't go through.
            if failed and not retried:
                exit_code = 2
        else:
            log("✔ All strategies healthy — nothing to retry.")

    except Exception as e:
        log(f"✖ Error: {e}")
        try:
            driver.save_screenshot(os.path.join(_HERE, "retry_debug.png"))
        except Exception:
            pass
        if telegram_token and telegram_chat:
            send_telegram(
                telegram_token, telegram_chat,
                f"❌ <b>Tradetron retry script crashed</b>\n<code>{html.escape(str(e)[:300])}</code>",
            )
        exit_code = 1
    finally:
        try:
            driver.save_screenshot(os.path.join(_HERE, "retry_last_run.png"))
        except Exception:
            pass
        driver.quit()
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
