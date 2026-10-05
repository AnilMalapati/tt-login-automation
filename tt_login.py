"""Shared Tradetron login helpers for the automation scripts."""

import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LOGIN_URL = "https://tradetron.tech/login"


def log(msg):
    print(f"[{time.strftime('%X')}] {msg}", flush=True)


def prepare_browser(driver):
    """Block ad/consent noise and force open shadow roots before first navigation."""
    driver.execute_cdp_cmd("Network.enable", {})
    driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": [
        "*nextroll.com*", "*adroll.com*", "*nr-data.net*",
        "*d.adroll.com*", "*s.adroll.com*",
    ]})
    # Skip the new-theme welcome dialog on every page load.
    driver.execute_cdp_cmd("Network.setCookie", {
        "name": "tt_theme_welcome",
        "value": "1",
        "domain": "tradetron.tech",
        "path": "/",
    })
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
        document.cookie = 'tt_theme_welcome=1; path=/; max-age=31536000; SameSite=Lax';
        const _attachShadow = Element.prototype.attachShadow;
        Element.prototype.attachShadow = function(init) {
            return _attachShadow.call(this, { ...init, mode: 'open' });
        };
        const closeWelcome = () => {
            const d = document.getElementById('tt-welcome');
            if (!d) return;
            try { d.close(); } catch (e) {}
            d.removeAttribute('open');
        };
        document.addEventListener('DOMContentLoaded', closeWelcome);
        new MutationObserver(closeWelcome).observe(document.documentElement, {
            childList: true, subtree: true
        });
    """})
    log("   Browser prep: NextRoll blocked, welcome cookie set, shadow DOM patched.")


def dismiss_overlays(driver):
    """Close theme welcome, cookie banner, and language nudge if they appear."""
    result = driver.execute_script("""
        const out = [];

        const welcomeBtn = document.querySelector('[data-welcome-continue], [data-welcome-close]');
        if (welcomeBtn) { welcomeBtn.click(); out.push('welcome-click'); }
        const dialog = document.getElementById('tt-welcome');
        if (dialog) {
            try { dialog.close(); } catch (e) {}
            dialog.removeAttribute('open');
            out.push('welcome-closed');
        }
        document.cookie = 'tt_theme_welcome=1; path=/; max-age=31536000; SameSite=Lax';

        const decline = [...document.querySelectorAll('a, button, div.adroll_consent_button')]
            .find(el => /decline all/i.test((el.textContent || '').trim()));
        if (decline) { decline.click(); out.push('cookie-declined'); }

        const eng = [...document.querySelectorAll('a')]
            .find(el => /continue in english/i.test((el.textContent || '').trim()));
        if (eng) { eng.click(); out.push('lang-english'); }

        // Hide sticky theme switcher / residual overlays that can steal clicks.
        for (const sel of ['.adroll_consent_container', '.tt-tsw']) {
            const el = document.querySelector(sel);
            if (el) { el.style.display = 'none'; out.push('hide:' + sel); }
        }
        return out.length ? out.join(',') : 'none';
    """)
    log(f"   Overlays: {result}")


def fill_input(driver, wait, css, value):
    """Fill a visible input; fall back to JS if Selenium can't interact."""
    el = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, css)))
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
        el.clear()
        el.send_keys(value)
        return "selenium"
    except Exception as e:
        log(f"   Selenium fill failed for {css} ({e.__class__.__name__}) — using JS.")
        driver.execute_script("""
            const el = document.querySelector(arguments[0]);
            if (!el) throw new Error('missing ' + arguments[0]);
            el.focus();
            el.value = arguments[1];
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        """, css, value)
        return "js"


def solve_altcha(driver):
    log("   Handling ALTCHA captcha...")
    altcha_triggered = False
    for attempt in range(8):
        result = driver.execute_script("""
            var widget = document.querySelector('form.auth-form altcha-widget, altcha-widget');
            if (!widget) return 'no-widget';
            if (typeof widget.verify === 'function') {
                try { widget.verify(); return 'verify-called'; } catch (e) {}
            }
            var root = widget.shadowRoot || widget;
            var cb = root.querySelector('input[type="checkbox"]');
            if (!cb) return 'no-checkbox';
            cb.click();
            return 'clicked';
        """)
        log(f"   ALTCHA attempt {attempt + 1}: {result}")
        if result in ("clicked", "verify-called"):
            altcha_triggered = True
            break
        time.sleep(1)

    if not altcha_triggered:
        cbs = driver.find_elements(
            By.CSS_SELECTOR, "form.auth-form altcha-widget input[type='checkbox']"
        )
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
                var w = document.querySelector('form.auth-form altcha-widget, altcha-widget');
                if (!w) return 'no-widget';
                var d = w.querySelector('[data-state]');
                return d ? d.getAttribute('data-state') : (w.getAttribute('state') || 'pending');
            """)
            log(f"   ALTCHA state: {state}")
            if state == "verified":
                log("   ✔ ALTCHA verified.")
                break


def tradetron_login(driver, wait, email, password):
    """Log into Tradetron, handling the redesigned login + welcome modal."""
    prepare_browser(driver)
    log("Logging into Tradetron...")
    driver.get(LOGIN_URL)
    time.sleep(1.5)
    dismiss_overlays(driver)

    fill_input(driver, wait, "form.auth-form #email, #email", email)
    fill_input(driver, wait, "form.auth-form #password, #password", password)
    dismiss_overlays(driver)  # cookie banner can appear after focus

    try:
        solve_altcha(driver)
    except Exception as e:
        log(f"   ALTCHA step error: {e} — continuing anyway...")

    time.sleep(0.5)
    dismiss_overlays(driver)
    before_url = driver.current_url

    submit = wait.until(EC.element_to_be_clickable((
        By.CSS_SELECTOR,
        "form.auth-form button[type='submit'], button.auth-card__cta, button[type='submit']"
    )))
    try:
        submit.click()
    except Exception:
        driver.execute_script("arguments[0].click();", submit)

    WebDriverWait(driver, 25).until(lambda d: d.current_url != before_url)
    time.sleep(2)
    dismiss_overlays(driver)
    log(f"✔ Logged in — {driver.current_url}")
