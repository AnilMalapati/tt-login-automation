#!/usr/bin/env python3
"""
Tradetron - Live Auto Strategies PNL Update (Telegram)

Logs in, reads every page of the deployed-strategies list and sends ONE
Telegram message with today's P&L per Live Auto strategy.

Usage:
  python tradetron_pnl_report.py --scope all      # NFO/BFO first, MCX last (4 PM IST)
  python tradetron_pnl_report.py --scope mcx      # MCX only          (11:25 PM IST)
  python tradetron_pnl_report.py --force          # bypass weekday check
  python tradetron_pnl_report.py --dump           # save page text to pnl_debug.txt

IMPORTANT - written without access to the live page markup. The row parser
(parse_rows) works on the visible text of each table row and is deliberately
strict: if it cannot find strategy names, statuses and a P&L number in a row,
the row is skipped, and if NO row parses the script sends a failure message
instead of a report. A wrong report is worse than no report. Check
pnl_debug.txt (uploaded as an artifact by the workflow) and adjust
parse_rows() after the first run.

MCX detection from the list page is by name keyword only (MCX_KEYWORDS); the
page does not show the exchange.
"""

import argparse
import datetime
import os
import re
import sys
import time

import pytz
from dotenv import load_dotenv
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from tradetron_error_retry import DEPLOYED_URL, _goto_page, build_driver, send_telegram
from tt_login import log, tradetron_login

_HERE = os.path.dirname(os.path.abspath(__file__))
IST = pytz.timezone("Asia/Kolkata")
MAX_PAGES = 20

STATUSES = ["Live-Entered", "Live Entered", "Active", "Exited", "Paused",
            "Error-Execution", "Error Execution", "Error", "Blocked", "Archived"]
MCX_KEYWORDS = ("gold", "silver", "crude", "natural gas", "naturalgas", "copper",
                "zinc", "nickel", "lead", "aluminium", "mcx")
MONEY_RE = re.compile(r"([+\-−]?)\s*(?:₹|INR|Rs\.?)?\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


def is_weekday_ist():
    return datetime.datetime.now(IST).weekday() < 5


def is_mcx(name):
    n = name.lower()
    return any(k in n for k in MCX_KEYWORDS)


def _money(text):
    """First signed amount in text, as float, or None."""
    m = MONEY_RE.search(text.replace(",", ""))
    if not m:
        return None
    val = float(m.group(2))
    return -val if m.group(1) in ("-", "−") else val


def parse_rows(driver):
    """Return a list of dicts {name, status, pnl} for the rows on the current
    page. Strict: rows that do not look like a strategy row are skipped."""
    out = []
    for row in driver.find_elements(By.CSS_SELECTOR, "table tbody tr"):
        cells = [c.text.strip() for c in row.find_elements(By.CSS_SELECTOR, "td")]
        cells = [c for c in cells if c]
        if len(cells) < 3:
            continue
        joined = " | ".join(cells)
        status = next((s for s in STATUSES if re.search(rf"\b{re.escape(s)}\b", joined, re.I)), None)
        if not status:
            continue
        if "Live Auto" not in joined and "LIVE AUTO" not in joined.upper():
            continue
        name = next((c for c in cells
                     if not re.fullmatch(r"[\d,.\s+\-−₹%]+", c)
                     and c.lower() not in (s.lower() for s in STATUSES)
                     and "live" not in c.lower()[:5]), None)
        pnl_cell = next((c for c in cells if "₹" in c or re.search(r"[+\-−]\s*\d", c)), None)
        pnl = _money(pnl_cell) if pnl_cell else None
        if not name or pnl is None:
            continue
        out.append({"name": name.split("\n")[0].strip(), "status": status.replace(" ", "-")
                    if status in ("Live Entered", "Error Execution") else status, "pnl": pnl})
    return out


def _schema(obj, path="$", out=None, depth=0):
    """Field paths and types only. NEVER values: the repo is public and so are
    its Actions logs."""
    out = [] if out is None else out
    if depth > 6 or len(out) > 220:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _schema(v, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        out.append(f"{path}[] len={len(obj)}")
        for v in obj[:1]:
            _schema(v, f"{path}[]", out, depth + 1)
    else:
        out.append(f"{path}: {type(obj).__name__}")
    return out


def log_page_shape(driver):
    """Debug aid: prints the SHAPE (names and types, no values) of the
    dashboard's own JSON endpoints."""
    import json as _json
    for ep in ("/api/deployments", "/api/deployments/summary"):
        try:
            res = driver.execute_async_script(
                "const done=arguments[arguments.length-1];"
                "fetch(arguments[0],{credentials:'include',headers:{'Accept':'application/json'}})"
                ".then(async r=>done({status:r.status,body:await r.text()}))"
                ".catch(e=>done({status:-1,body:String(e)}));", ep)
            log(f"[api] {ep} status={res.get('status')} bytes={len(res.get('body',''))}")
            try:
                for ln in _schema(_json.loads(res["body"])):
                    log("[api|] " + ln)
            except Exception as e:
                log(f"[api] not json: {e}")
        except Exception as e:
            log(f"[api] {ep} failed: {e}")


def collect(driver, dump=False):
    driver.get(DEPLOYED_URL)
    time.sleep(5)
    log_page_shape(driver)
    rows, texts = [], []
    for page in range(1, MAX_PAGES + 1):
        texts.append(f"=== page {page} ===\n" + driver.find_element(By.TAG_NAME, "body").text)
        rows.extend(parse_rows(driver))
        if not _goto_page(driver, page + 1):
            break
    if dump:
        with open(os.path.join(_HERE, "pnl_debug.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(texts))
    return rows


def _dot(v):
    return "\U0001f7e2" if v > 0 else ("\U0001f534" if v < 0 else "⚪")


def _inr(v):
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    s = f"{abs(v):,.2f}".rstrip("0").rstrip(".") if abs(v) % 1 else f"{abs(v):,.0f}"
    return f"{sign} ₹{s}"


def _merge(rows):
    merged = {}
    for r in rows:
        k = (r["name"], r["status"])
        if k in merged:
            merged[k]["pnl"] += r["pnl"]
            merged[k]["n"] += 1
        else:
            merged[k] = {**r, "n": 1}
    return list(merged.values())


def _section(title, rows):
    if not rows:
        return []
    rows = _merge(rows)
    live = sorted([r for r in rows if r["pnl"] != 0], key=lambda r: -r["pnl"])
    flat = sorted([r for r in rows if r["pnl"] == 0], key=lambda r: r["name"].lower())
    lines = [f"⚡ {title}", ""]
    for r in live + flat:
        nm = r["name"] + (f" (x{r['n']})" if r["n"] > 1 else "")
        lines += [nm, f"{r['status']} · Live Auto", f"{_dot(r['pnl'])} {_inr(r['pnl'])}", ""]
    return lines


def build_message(rows, scope, now_str):
    rows = [r for r in rows if r["status"] != "Archived"]
    mcx = [r for r in rows if is_mcx(r["name"])]
    nfo = [r for r in rows if not is_mcx(r["name"])]
    title = "MCX Live Auto Strategies PNL Update" if scope == "mcx" else "Live Auto Strategies PNL Update"
    use = mcx if scope == "mcx" else rows
    lines = [title, now_str, "-" * 30]
    if scope == "all":
        lines += _section("NFO / BFO", nfo)
    lines += _section("MCX", mcx)
    lines.append("-" * 30)
    merged = _merge(use)
    total = sum(r["pnl"] for r in use)
    lines.append(f"{'MCX total' if scope == 'mcx' else 'Overall'}: {_dot(total)} {_inr(total)}")
    nz = sorted([r for r in merged if r["pnl"] != 0], key=lambda r: -r["pnl"])
    if nz:
        lines.append("Top 3: " + ", ".join(f"{r['name']} {_inr(r['pnl'])}" for r in nz[:3]))
        lines.append("Bottom 3: " + ", ".join(f"{r['name']} {_inr(r['pnl'])}" for r in nz[::-1][:3]))
    bad = [r["name"] for r in use if re.search(r"error|blocked", r["status"], re.I)]
    lines.append("Attention: " + (", ".join(bad) if bad else "none"))
    counts = {}
    for r in use:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines.append("Status: " + " | ".join(f"{n} {s}" for s, n in sorted(counts.items())) +
                 f" ({len(use)} live auto, archived excluded)")
    lines.append("(x2) = two deployments of the same strategy")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["all", "mcx"], default="all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    load_dotenv(os.path.join(_HERE, ".env"))
    if not args.force and not is_weekday_ist():
        log("Weekend (IST) - skipping. Use --force to override.")
        sys.exit(0)

    email = os.getenv("TRADETRON_EMAIL", "").strip()
    password = os.getenv("TRADETRON_PASSWORD", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not email or not password:
        log("ERROR: Set TRADETRON_EMAIL and TRADETRON_PASSWORD")
        sys.exit(1)

    driver = build_driver(headless=not args.headed)
    wait = WebDriverWait(driver, 30)
    code = 0
    try:
        tradetron_login(driver, wait, email, password)
        rows = collect(driver, dump=args.dump)
        if not rows:
            raise RuntimeError("no strategy rows could be read from the deployed-strategies page "
                               "(page layout may have changed); no report sent")
        now_str = datetime.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
        msg = build_message(rows, args.scope, now_str)
        log(msg)
        if token and chat:
            # plain text on purpose: names may contain characters that break HTML mode
            import requests
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": msg[:4000]}, timeout=15)
            if r.status_code != 200:
                raise RuntimeError(f"Telegram error: {r.text[:200]}")
        else:
            log("(No Telegram credentials set - skipping notification)")
            code = 1
    except Exception as e:
        log(f"Error: {e}")
        try:
            collect_text = driver.find_element(By.TAG_NAME, "body").text
            with open(os.path.join(_HERE, "pnl_debug.txt"), "w", encoding="utf-8") as f:
                f.write(collect_text)
            driver.save_screenshot(os.path.join(_HERE, "pnl_debug.png"))
        except Exception:
            pass
        if token and chat:
            send_telegram(token, chat, f"❌ <b>Tradetron P&amp;L report failed</b>\n<code>{str(e)[:300]}</code>")
        code = 1
    finally:
        driver.quit()
        sys.exit(code)


if __name__ == "__main__":
    main()
