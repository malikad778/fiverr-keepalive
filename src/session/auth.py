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
from ..utils.config import load_config, get
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


async def is_logged_in(page: Page, context: Optional[BrowserContext] = None) -> bool:
    """Check if the current page shows a logged-in session, solving challenges if encountered."""
    try:
        cfg = load_config()
        username = cfg["_env"]["username"]
        base_url  = cfg["target"]["base_url"]

        await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Check for PerimeterX / Human Touch challenge and solve if needed
        from ..behavior.challenge import is_challenge_present, solve_human_touch
        if await is_challenge_present(page):
            log.info("auth.challenge_detected_during_login_check")
            await solve_human_touch(page, context)
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


# Fiverr's /login is SSO-first: it renders no credential inputs at all until
# "Continue with email/username" is clicked. Matching a bare "Continue" hits
# "Continue with Google" instead, which is what silently broke this before.
_EMAIL_OPTION_SELECTORS = (
    "button:has-text('Continue with email/username')",
    "[role='button']:has-text('Continue with email/username')",
    "button:has-text('Continue with email')",
)
# Stable hooks from the real form — the CSS classes are hashed and churn.
_USERNAME_FIELD = "#identification-usernameOrEmail, input[name='usernameOrEmail']"
_PASSWORD_FIELD = "#identification-password, input[name='password'][type='password']"
_LOGIN_SUBMIT = "form button[type='submit']"


async def _wait_visible(page: Page, selector: str, timeout: float = 15.0) -> bool:
    """Poll until a selector is present and visible."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.4)
    return False


async def login(page: Page, context: BrowserContext) -> bool:
    """
    Perform the full email/username + password login flow.

    Requires headed mode — PerimeterX will not process the press-and-hold
    challenge in headless Chromium, and /login is where it challenges hardest.
    """
    cfg = load_config()
    email    = cfg["_env"]["email"]
    password = cfg["_env"]["password"]
    base_url = cfg["target"]["base_url"]

    if not email or not password:
        log.error("auth.missing_credentials")
        raise ValueError("FIVERR_EMAIL and FIVERR_PASSWORD must be set in .env")

    from ..behavior.challenge import is_challenge_present, solve_human_touch

    try:
        log.info("auth.navigating_to_login")
        await page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(random.uniform(3, 5))

        # Accept cookies if banner appears
        try:
            cookie_btn = page.locator("button:has-text('Accept'), [data-testid='accept-cookies']")
            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        if await is_challenge_present(page):
            log.info("auth.challenge_on_login_page")
            if not await solve_human_touch(page, context):
                log.error("auth.login_blocked_by_challenge")
                return False
            await asyncio.sleep(random.uniform(2, 4))

        # 1. Reveal the credential form.
        opened = False
        for sel in _EMAIL_OPTION_SELECTORS:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    log.info("auth.opening_credential_form", selector=sel)
                    await loc.click()
                    opened = True
                    break
            except Exception:
                continue

        if not opened:
            log.warning("auth.email_option_not_found_trying_direct")

        # 2. Wait for the form to mount.
        if not await _wait_visible(page, _USERNAME_FIELD, timeout=15.0):
            log.error("auth.username_field_never_appeared", url=page.url)
            return False

        await _type_humanlike(page, _USERNAME_FIELD, email)
        await simulate_typing_pause(1.2)

        if not await _wait_visible(page, _PASSWORD_FIELD, timeout=10.0):
            log.error("auth.password_field_never_appeared", url=page.url)
            return False

        await _type_humanlike(page, _PASSWORD_FIELD, password)
        await simulate_typing_pause(0.8)

        # 3. Submit. The button ships disabled and only enables once both
        #    fields validate, so clicking too early is a silent no-op.
        submit = page.locator(_LOGIN_SUBMIT).first
        for _ in range(20):
            try:
                if await submit.count() > 0 and await submit.is_enabled():
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            log.warning("auth.submit_never_enabled")

        try:
            await submit.click()
            log.info("auth.credentials_submitted")
        except Exception as e:
            log.error("auth.submit_click_failed", error=str(e))
            return False

        await asyncio.sleep(random.uniform(5, 8))

        # 4. A challenge or a step-up verification can appear post-submit.
        if await is_challenge_present(page):
            log.info("auth.challenge_after_submit")
            await solve_human_touch(page, context)
            await asyncio.sleep(random.uniform(2, 4))

        # Step-up verification. Fiverr routes to /mfa/generate with the bare
        # title "Mfa" and no give-away body copy, so the URL is the reliable
        # signal — a body-text scan alone misses it entirely.
        url_now = (page.url or "").lower()
        if "/mfa" in url_now or "/two_factor" in url_now or "/verify" in url_now:
            log.error(
                "auth.mfa_required",
                url=page.url,
                hint="password is correct but Fiverr demands an emailed/SMS code; "
                     "automated login cannot proceed — re-capture cookies with "
                     "scripts/local_auth.py on a machine that can complete MFA",
            )
            return False

        try:
            body = (await page.evaluate("() => document.body.innerText || ''")).lower()
            for marker in ("verification code", "verify your email", "we sent a code",
                           "enter the code", "two-factor", "security code",
                           "no longer valid"):
                if marker in body:
                    log.error("auth.step_up_verification_required", marker=marker,
                              hint="cannot be automated — re-capture cookies with scripts/local_auth.py")
                    return False
        except Exception:
            pass

        if await is_logged_in(page, context):
            log.info("auth.login_success")
            await save_cookies(context)
            return True

        log.error("auth.login_failed_no_session", url=page.url)
        return False

    except Exception as e:
        log.error("auth.login_exception", error=str(e))
        return False


async def ensure_logged_in(page: Page, context: BrowserContext) -> bool:
    """
    Check if session is valid; attempt auto-login from stored credentials
    if session has expired. Returns True if session is usable.
    """
    from .manager import load_cookies, strip_px_cookies

    # 1. Load stored cookies, then drop any PerimeterX identity the
    #    persistent profile is still carrying on disk. A flagged _pxvid makes
    #    PX refuse to serve a solvable challenge regardless of egress IP.
    loaded = await load_cookies(context)
    await strip_px_cookies(context)

    if loaded:
        # 2. Verify the session is actually still valid
        if await is_logged_in(page, context):
            log.info("auth.session_valid_from_cookies")
            return True

        log.warning("auth.cookies_loaded_but_session_invalid")

        # 3. Challenge recovery: wipe Fiverr data (keeping the Google SSO
        #    cookies), solve the fresh challenge, re-login via Google.
        #    recover_session_after_challenge() verifies the session itself.
        from ..behavior.challenge import recover_session_after_challenge
        if await recover_session_after_challenge(page, context):
            log.info("auth.session_recovered_successfully")
            return True

        # NOTE: deliberately not calling clear_session() here. The stored
        # blob is the only copy of the Google SSO cookies, and wiping it
        # turns a recoverable session into a manual re-auth. A successful
        # login overwrites the row anyway.

    # 4. Before burning login attempts, make sure "not logged in" isn't just
    #    "cannot see the page". A PerimeterX block makes is_logged_in() return
    #    False even when the stored cookies are perfectly valid — and no login
    #    can succeed through a block anyway, so attempting one only adds
    #    blocked requests, MFA mails, and reputation damage.
    from ..behavior.challenge import is_challenge_present, _in_cooldown

    if await is_challenge_present(page) or _in_cooldown():
        log.error(
            "auth.blocked_cannot_verify_session",
            hint="PerimeterX is blocking the page, so login state is unknown "
                 "and no login can complete; leaving stored cookies intact "
                 "and backing off rather than re-authenticating",
        )
        return False

    # 5. Re-login — prefer Google SSO, fall back to password.
    from .google_auth import login_with_google

    max_attempts = get("session.max_recovery_attempts", 3)
    for attempt in range(1, max_attempts + 1):
        log.info("auth.attempting_login", attempt=attempt)

        if await login_with_google(page, context):
            log.info("auth.google_login_success", attempt=attempt)
            return True

        log.warning("auth.google_login_failed_trying_password", attempt=attempt)
        try:
            if await login(page, context):
                return True
        except ValueError as e:
            # Missing FIVERR_EMAIL/PASSWORD — no point retrying.
            log.error("auth.password_login_unavailable", error=str(e))
            break

        await asyncio.sleep(30 * attempt)

    log.error("auth.all_login_attempts_failed")
    return False
