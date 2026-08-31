"""
src/session/auth.py
Handles Fiverr login flow — used only on first run or after session expiry.
Designed for interactive (headed) use. After login, saves cookies and
switches to headless for all future runs.
"""
import asyncio
import random
import os
from typing import Optional

from playwright.async_api import Page, BrowserContext

from .manager import save_cookies, clear_session
from ..behavior.mouse import move_mouse_to, click_at
from ..behavior.idle import simulate_typing_pause
from ..utils.config import load_config
from ..utils.logger import get_logger

log = get_logger("session.auth")


async def _type_humanlike(page: Page, selector: str, text: str) -> None:
    """Type into a field character by character with human-like timing."""
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.7))
    for char in text:
        await page.keyboard.type(char)
        # Realistic per-character delay: 50-200ms with occasional longer pause
        delay = random.uniform(0.05, 0.18)
        if random.random() < 0.05:
            delay += random.uniform(0.3, 0.8)  # brief thinking pause
        await asyncio.sleep(delay)


async def is_logged_in(page: Page) -> bool:
    """Check if the current page shows a logged-in session."""
    try:
        cfg = load_config()
        username = cfg["_env"]["username"]
        base_url  = cfg["target"]["base_url"]

        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Multiple indicators of a logged-in state
        checks = [
            page.locator(f"[href*='/users/{username}']").count(),
            page.locator(".user-profile-menu").count(),
            page.locator("[data-testid='user-avatar']").count(),
            page.locator(".profile-image").count(),
        ]
        results = await asyncio.gather(*checks, return_exceptions=True)
        logged_in = any(r > 0 for r in results if isinstance(r, int))
        log.info("auth.login_check", logged_in=logged_in)
        return logged_in
    except Exception as e:
        log.warning("auth.login_check_error", error=str(e))
        return False


async def login(page: Page, context: BrowserContext) -> bool:
    """
    Perform full login flow.
    Should be called in headed mode (headless=False) for first run.
    """
    cfg = load_config()
    email    = cfg["_env"]["email"]
    password = cfg["_env"]["password"]
    base_url = cfg["target"]["base_url"]

    if not email or not password:
        log.error("auth.missing_credentials")
        raise ValueError("FIVERR_EMAIL and FIVERR_PASSWORD must be set in .env")

    try:
        log.info("auth.navigating_to_login")
        await page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))

        # Accept cookies if banner appears
        try:
            cookie_btn = page.locator("button:has-text('Accept'), [data-testid='accept-cookies']")
            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        # Email field
        email_selectors = [
            "input[name='email']",
            "input[type='email']",
            "#email",
            "[placeholder*='email' i]",
        ]
        for sel in email_selectors:
            if await page.locator(sel).count() > 0:
                await _type_humanlike(page, sel, email)
                break

        await simulate_typing_pause(1.5)

        # Password field
        pwd_selectors = [
            "input[name='password']",
            "input[type='password']",
            "#password",
        ]
        for sel in pwd_selectors:
            if await page.locator(sel).count() > 0:
                await _type_humanlike(page, sel, password)
                break

        await simulate_typing_pause(0.8)

        # Submit
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Continue')",
            "button:has-text('Sign in')",
            "button:has-text('Log in')",
            "[data-testid='submit']",
        ]
        for sel in submit_selectors:
            if await page.locator(sel).count() > 0:
                await page.locator(sel).first.click()
                break

        # Wait for redirect after login
        await asyncio.sleep(random.uniform(4, 8))

        # Verify login succeeded
        if await is_logged_in(page):
            log.info("auth.login_success")
            await save_cookies(context)
            return True
        else:
            log.error("auth.login_failed_no_session")
            return False

    except Exception as e:
        log.error("auth.login_exception", error=str(e))
        return False


async def ensure_logged_in(page: Page, context: BrowserContext) -> bool:
    """
    Check if session is valid; attempt auto-login from stored credentials
    if session has expired. Returns True if session is usable.
    """
    from .manager import load_cookies

    # 1. Load stored cookies
    loaded = await load_cookies(context)

    if loaded:
        # 2. Verify the session is actually still valid
        if await is_logged_in(page):
            log.info("auth.session_valid_from_cookies")
            return True
        log.warning("auth.cookies_loaded_but_session_invalid")
        await clear_session()

    # 3. Re-login
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        log.info("auth.attempting_login", attempt=attempt)
        success = await login(page, context)
        if success:
            return True
        await asyncio.sleep(30 * attempt)

    log.error("auth.all_login_attempts_failed")
    return False
