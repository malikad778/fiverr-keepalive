#!/usr/bin/env python3
"""
scripts/test_login.py
Cold "incognito" login test: throwaway profile, no stored cookies, real
credentials from .env. Exercises the password path end-to-end, including any
PerimeterX challenge that appears on /login.

Safe by default: store.db is NOT written unless --save is passed. login()
normally calls save_cookies() on success, which does DELETE-then-INSERT on the
shared store, so an unguarded test run would replace a known-good session with
whatever this probe happened to produce.

Usage:
    python scripts/test_login.py                    # cold login, no cookie write
    python scripts/test_login.py --save             # also persist on success
    python scripts/test_login.py --keep-open 60     # linger so you can screenshot
"""
import sys
import asyncio
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

import src.session.auth as auth_mod
from src.utils.config import load_config
from src.utils.logger import setup_logging
from src.behavior.challenge import is_challenge_present, handle_challenge

_ROOT = Path(__file__).resolve().parent.parent


EMAIL_SELECTORS = [
    "input[name='email']", "input[type='email']", "#email",
    "[placeholder*='email' i]", "input[name='username']", "#login-form input",
]
PWD_SELECTORS = ["input[name='password']", "input[type='password']", "#password"]
SUBMIT_SELECTORS = [
    "button[type='submit']", "button:has-text('Continue')",
    "button:has-text('Sign in')", "button:has-text('Log in')",
    "[data-testid='submit']",
]
ERROR_SELECTORS = [
    "[role='alert']", ".error", ".error-message", ".form-error",
    "[class*='error' i]", "[data-testid*='error' i]",
]


async def _report(page, label):
    """Print which selectors match right now, so we can see the real form."""
    print(f"    --- {label} ---")
    for group, sels in (("email", EMAIL_SELECTORS),
                        ("password", PWD_SELECTORS),
                        ("submit", SUBMIT_SELECTORS)):
        hits = []
        for s in sels:
            try:
                n = await page.locator(s).count()
                if n:
                    vis = await page.locator(s).first.is_visible()
                    hits.append(f"{s}(n={n},vis={vis})")
            except Exception:
                pass
        print(f"      {group:9s}: {hits if hits else 'NO MATCH'}")


async def stepwise_login(page, context, env, cfg, out_dir, stamp):
    """Drive the login form by hand, capturing state at each step."""
    base_url = cfg["target"]["base_url"]
    print("\n[*] Stepwise login — navigating to /login")
    await page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(4)

    if await is_challenge_present(page):
        print("[*] Challenge on /login — solving before touching the form")
        print(f"[{'+' if await handle_challenge(page, context) else '!'}] challenge handled")
        await asyncio.sleep(3)

    print(f"    url   : {page.url}")
    print(f"    title : {await page.title()!r}")
    await page.screenshot(path=str(out_dir / f"login-step1-{stamp}.png"), full_page=True)
    await _report(page, "selectors on /login")

    # Email
    typed_email = False
    for s in EMAIL_SELECTORS:
        try:
            if await page.locator(s).count() and await page.locator(s).first.is_visible():
                await page.locator(s).first.click()
                await page.locator(s).first.type(env["email"], delay=90)
                print(f"    [+] typed email into {s}")
                typed_email = True
                break
        except Exception as e:
            print(f"    [!] {s}: {e}")
    if not typed_email:
        print("    [!] NO EMAIL FIELD FOUND — form never filled")

    await asyncio.sleep(1.5)

    # Password may only appear after submitting the email (two-step form).
    if not any([await page.locator(s).count() for s in PWD_SELECTORS]):
        print("    [*] no password field yet — trying Continue (two-step form?)")
        for s in SUBMIT_SELECTORS:
            try:
                if await page.locator(s).count() and await page.locator(s).first.is_visible():
                    await page.locator(s).first.click()
                    print(f"    [+] clicked {s}")
                    break
            except Exception:
                pass
        await asyncio.sleep(4)
        await _report(page, "after email submit")

    typed_pwd = False
    for s in PWD_SELECTORS:
        try:
            if await page.locator(s).count() and await page.locator(s).first.is_visible():
                await page.locator(s).first.click()
                await page.locator(s).first.type(env["password"], delay=90)
                print(f"    [+] typed password into {s}")
                typed_pwd = True
                break
        except Exception as e:
            print(f"    [!] {s}: {e}")
    if not typed_pwd:
        print("    [!] NO PASSWORD FIELD FOUND")

    await page.screenshot(path=str(out_dir / f"login-step2-{stamp}.png"), full_page=True)
    await asyncio.sleep(1)

    for s in SUBMIT_SELECTORS:
        try:
            if await page.locator(s).count() and await page.locator(s).first.is_visible():
                await page.locator(s).first.click()
                print(f"    [+] submitted via {s}")
                break
        except Exception:
            pass

    await asyncio.sleep(8)

    # Capture the result BEFORE anything navigates away.
    print(f"\n    post-submit url   : {page.url}")
    print(f"    post-submit title : {await page.title()!r}")
    await page.screenshot(path=str(out_dir / f"login-step3-{stamp}.png"), full_page=True)

    print("    visible error text:")
    seen = set()
    for s in ERROR_SELECTORS:
        try:
            for i in range(min(await page.locator(s).count(), 5)):
                el = page.locator(s).nth(i)
                if await el.is_visible():
                    t = (await el.inner_text() or "").strip()
                    if t and t not in seen and len(t) < 300:
                        seen.add(t)
                        print(f"      - {t!r}")
        except Exception:
            pass
    if not seen:
        print("      (none found)")

    body = (await page.evaluate("() => document.body.innerText || ''"))[:600]
    print(f"\n    body text head:\n{body}")
    return 0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="/tmp/login-test",
                    help="throwaway profile dir (never the daemon's)")
    ap.add_argument("--save", action="store_true",
                    help="allow writing cookies to store.db on success")
    ap.add_argument("--headless", action="store_true",
                    help="force headless (expected to FAIL the challenge)")
    ap.add_argument("--keep-open", type=int, default=0,
                    help="seconds to keep the browser open at the end")
    ap.add_argument("--steps", action="store_true",
                    help="drive the form manually, reporting each selector match "
                         "and capturing the page state right after submit "
                         "(login() calls is_logged_in(), which navigates away "
                         "and destroys any error message before we can read it)")
    args = ap.parse_args()

    setup_logging("INFO")
    cfg = load_config()
    env = cfg["_env"]

    if not env.get("email") or not env.get("password"):
        print("[!] FIVERR_EMAIL / FIVERR_PASSWORD not set in .env — nothing to test")
        return 2

    saved = []
    if not args.save:
        # Neutralise the cookie write inside login() for this process only.
        async def _no_save(context, domain="fiverr.com"):
            cookies = await context.cookies()
            saved.append(len(cookies))
            print(f"[*] save_cookies() suppressed (would have stored {len(cookies)} cookies)")
        auth_mod.save_cookies = _no_save

    out_dir = _ROOT / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    print("=" * 62)
    print("  Cold login test (incognito-equivalent)")
    print("=" * 62)
    print(f"  email    : {env['email'][:3]}***@{env['email'].split('@')[-1]}")
    print(f"  profile  : {args.profile} (throwaway, no cookies loaded)")
    print(f"  headless : {args.headless}")
    print(f"  store.db : {'WILL be written on success' if args.save else 'protected'}")
    print("=" * 62)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            args.profile,
            headless=args.headless,
            viewport={"width": 1366, "height": 768},
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            if args.steps:
                return await stepwise_login(page, context, env, cfg, out_dir, stamp)

            print("\n[*] Running login() with no prior session ...")
            ok = await auth_mod.login(page, context)

            print(f"\n[{'+' if ok else '!'}] login() -> {ok}")
            print(f"    final url   : {page.url}")
            print(f"    final title : {await page.title()!r}")

            blocked = await is_challenge_present(page)
            print(f"    challenged  : {blocked}")

            if blocked:
                print("\n[*] Challenge on the login page — invoking handle_challenge() ...")
                cleared = await handle_challenge(page, context)
                print(f"[{'+' if cleared else '!'}] handle_challenge -> {cleared}")
                if cleared:
                    ok = await auth_mod.is_logged_in(page, context)
                    print(f"    logged_in after clearing: {ok}")

            shot = out_dir / f"login-test-{stamp}.png"
            await page.screenshot(path=str(shot), full_page=True)
            print(f"\n    screenshot -> {shot}")

            if args.keep_open:
                print(f"[*] Holding browser open {args.keep_open}s ...")
                await asyncio.sleep(args.keep_open)

            return 0 if ok else 1
        finally:
            await context.close()


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.")
