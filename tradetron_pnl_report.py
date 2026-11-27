#!/usr/bin/env python3
"""
Tradetron - Live Auto Strategies PNL Update (Telegram)

Logs in (Selenium, needed for the captcha), then reads the dashboard's own
JSON endpoint (/api/deployments, paginated) from inside the logged-in browser
and sends ONE plain-text Telegram message with today's P&L per Live Auto
strategy.

Usage:
  python tradetron_pnl_report.py --scope all    # NFO/BFO first, MCX last (4 PM IST)
  python tradetron_pnl_report.py --scope mcx    # MCX only                (11:25 PM IST)
  python tradetron_pnl_report.py --force        # bypass weekday check

PRIVACY: this repo is public and so are its Actions logs and artifacts. This
script therefore never logs strategy names, P&L or page text, only counts, and
the workflow uploads no artifacts. The report goes to Telegram only.

P&L is the `pnl` field of /api/deployments, i.e. the dashboard's default
Expiry view (booked + open, carried positions included).
"""

import argparse
import datetime
import os
import re
import sys

import pytz
import requests
from dotenv import load_dotenv
from selenium.webdriver.support.ui import WebDriverWait

from tradetron_error_retry import build_driver
from tt_login import log, tradetron_login

_HERE = os.path.dirname(os.path.abspath(__file__))
IST = pytz.timezone("Asia/Kolkata")
MAX_PAGES = 20


def is_weekday_ist():
    return datetime.datetime.now(IST).weekday() < 5


def fetch_deployments(driver):
    """All deployment rows from /api/deployments, following pagination."""
    rows, last_page = [], 1
    page = 1
    while page <= min(last_page, MAX_PAGES):
        res = driver.execute_async_script(
            "const done=arguments[arguments.length-1];"
            "fetch('/api/deployments?page='+arguments[0],"
            "{credentials:'include',headers:{'Accept':'application/json'}})"
            ".then(async r=>done({status:r.status,body:await r.text()}))"
            ".catch(e=>done({status:-1,body:String(e)}));", page)
        if res.get("status") != 200:
            raise RuntimeError(f"/api/deployments page {page} returned HTTP {res.get('status')}")
        import json
        data = json.loads(res["body"])
        if not data.get("success", True):
            raise RuntimeError("/api/deployments reported success=false")
        rows.extend(data.get("data", []))
        last_page = int((data.get("paginate") or {}).get("last_page") or 1)
        page += 1
    return rows


def to_entries(api_rows):
    """Live Auto, non-archived deployments as simple dicts."""
    out = []
    for d in api_rows:
        # Only deployments in the user's own account. Rows shared by other
        # creators ("Shared" tab on the dashboard) carry is_shared=true.
        if d.get("is_shared"):
            continue
        status = str(d.get("status") or "")
        dtype = str(d.get("deployment_type") or "").upper()
        if status.lower() == "archived":
            continue
        if "AUTO" not in dtype:
            continue
        out.append({
            "name": str(d.get("template_name") or "Unknown").strip(),
            "status": status,
            "pnl": float(d.get("pnl") or 0.0),
            "capital": float(d.get("capital") or 0.0),
            "open": int(d.get("open_position_count") or 0),
            "total": int(d.get("position_count") or 0),
            "mcx": "MCX" in str(d.get("exchange") or "").upper(),
        })
    return out


def _dot(v):
    return "\U0001f7e2" if v > 0 else ("\U0001f534" if v < 0 else "⚪")


def _inr(v):
    sign = "+" if v > 0 else ("-" if v < 0 else "")
    a = abs(v)
    s = f"{a:,.2f}".rstrip("0").rstrip(".") if round(a, 2) % 1 else f"{a:,.0f}"
    return (f"{sign} " if sign else "") + f"₹{s}"


def _pct(pnl, cap):
    if not cap:
        return ""
    p = pnl / cap * 100
    return f" ({'+' if p > 0 else ''}{p:.2f}%)"


def _merge(rows):
    merged = {}
    for r in rows:
        k = (r["name"], r["status"])
        if k in merged:
            m = merged[k]
            for f in ("pnl", "capital", "open", "total"):
                m[f] += r[f]
            m["n"] += 1
        else:
            merged[k] = {**r, "n": 1}
    return list(merged.values())


def _bracket(r):
    if r["open"] > 0:
        return f" [{r['open']} open]"
    if r["total"] > 0:
        return f" [0/{r['total']}]"
    return ""


def _section(title, rows):
    if not rows:
        return []
    rows = _merge(rows)
    live = sorted([r for r in rows if r["pnl"] != 0], key=lambda r: -r["pnl"])
    flat = sorted([r for r in rows if r["pnl"] == 0], key=lambda r: r["name"].lower())
    lines = [f"⚡ {title}", ""]
    for r in live + flat:
        nm = r["name"] + (f" (x{r['n']})" if r["n"] > 1 else "")
        lines += [nm, f"{r['status']} · Live Auto",
                  f"{_dot(r['pnl'])} {_inr(r['pnl'])}{_pct(r['pnl'], r['capital'])}{_bracket(r)}", ""]
    return lines


def build_message(entries, scope, now_str):
    mcx = [r for r in entries if r["mcx"]]
    nfo = [r for r in entries if not r["mcx"]]
    use = mcx if scope == "mcx" else entries
    title = "MCX Live Auto Strategies PNL Update" if scope == "mcx" else "Live Auto Strategies PNL Update"
    lines = [title, now_str, "-" * 30]
    if scope == "all":
        lines += _section("NFO / BFO", nfo)
    lines += _section("MCX", mcx)
    if not use:
        lines += ["No live auto strategies in this group.", ""]
    lines.append("-" * 30)
    total = sum(r["pnl"] for r in use)
    lines.append(f"{'MCX total' if scope == 'mcx' else 'Overall'}: {_dot(total)} {_inr(total)}")
    nz = sorted([r for r in _merge(use) if r["pnl"] != 0], key=lambda r: -r["pnl"])
    if nz:
        lines.append("Top 3: " + ", ".join(f"{r['name']} {_inr(r['pnl'])}" for r in nz[:3]))
        lines.append("Bottom 3: " + ", ".join(f"{r['name']} {_inr(r['pnl'])}" for r in nz[::-1][:3]))
    bad = sorted({r["name"] for r in use if re.search(r"error|blocked", r["status"], re.I)})
    lines.append("Attention: " + (", ".join(bad) if bad else "none"))
    counts = {}
    for r in use:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines.append("Status: " + " | ".join(f"{n} {s}" for s, n in sorted(counts.items())) +
                 f" ({len(use)} live auto, archived excluded)")
    lines.append("(x2) = two deployments of the same strategy")
    return "\n".join(lines)


def _chunks(text, limit=3800):
    """Split on blank lines so no message exceeds Telegram's 4096-char limit."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


def telegram(token, chat, text):
    for part in _chunks(text):
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": part[:4096]}, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"Telegram error HTTP {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["all", "mcx"], default="all")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--headed", action="store_true")
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
        api_rows = fetch_deployments(driver)
        entries = to_entries(api_rows)
        shared = sum(1 for d in api_rows if d.get("is_shared"))
        log(f"Read {len(api_rows)} deployments ({shared} shared, excluded), "
            f"{len(entries)} own live auto non-archived.")
        if not api_rows:
            raise RuntimeError("the deployments API returned no rows; no report sent")
        now_str = datetime.datetime.now(IST).strftime("%d %b %Y, %I:%M %p IST")
        msg = build_message(entries, args.scope, now_str)
        if token and chat:
            telegram(token, chat, msg)
            log("Telegram report sent.")
        else:
            log("(No Telegram credentials set - skipping notification)")
            code = 1
    except Exception as e:
        log(f"Error: {str(e)[:200]}")
        if token and chat:
            try:
                telegram(token, chat, f"❌ Tradetron P&L report failed\n{str(e)[:300]}")
            except Exception:
                pass
        code = 1
    finally:
        driver.quit()
        sys.exit(code)


if __name__ == "__main__":
    main()
