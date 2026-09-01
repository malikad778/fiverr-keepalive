"""
src/session/google_auth.py
"Continue with Google" login for Fiverr.

Used after a challenge recovery wipe: Fiverr's own cookies are gone, but the
Google SSO cookies were deliberately preserved, so this should complete in a
single click with no credential entry.

If Google *does* prompt for a password, GOOGLE_PASSWORD (or FIVERR_PASSWORD as
a fallback) is used. That path is a last resort — an account with 2FA will stop
here, which is logged loudly rather than retried blindly.
"""
import asyncio
import random
from typing import Optional

from playwright.async_api import Page, BrowserContext

from .manager import save_cookies
from ..utils.config import load_config
from ..utils.logger import get_logger

log = get_logger("session.google_auth")

_GOOGLE_BUTTON_SELECTORS = (
    "button:has-text('Continue with Google')",
    "a:has-text('Continue with Google')",
    "[data-testid='google-login']",
    "[data-testid='continue-with-google']",
    "button[aria-label*='Google' i]",
    "a[href*='/auth/google']",
    "a[href*='google_oauth']",
    ".google-login, .social-google, .btn-google",
)

_LOGIN_ENTRY_SELECTORS = (
    "a[href='/login']",
    "a[href*='/login']",
    "button:has-text('Sign in')",
    "a:has-text('Sign in')",
)


async def _dismiss_cookie_banner(page: Page) -> None:
    try:
        btn = page.locator(
            "button:has-text('Accept'), [data-testid='accept-cookies'], "
            "button:has-text('Got it')"
        )
        if await btn.count() > 0:
            await btn.first.click()
            await asyncio.sleep(random.uniform(0.5, 1.2))
    except Exception:
        pass


async def _click_google_button(page: Page) -> Optional[Page]:
    """
    Click the Google SSO button.

    Returns the page that now holds the Google flow: a popup if one opened,
    otherwise the original page (same-tab redirect). None if no button found.
    """
    for sel in _GOOGLE_BUTTON_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0 or not await loc.is_visible():
                continue

            log.info("google_auth.clicking_sso_button", selector=sel)

            # The flow may open a popup or redirect in place — handle both.
            try:
                async with page.context.expect_page(timeout=6000) as popup_info:
                    await loc.click()
                popup = await popup_info.value
                await popup.wait_for_load_state("domcontentloaded")
                log.info("google_auth.popup_opened", url=popup.url[:100])
                return popup
            except Exception:
                # No popup — assume same-tab redirect.
                await asyncio.sleep(random.uniform(2.0, 4.0))
                log.info("google_auth.same_tab_redirect", url=page.url[:100])
                return page
        except Exception as e:
            log.debug("google_auth.button_attempt_failed", selector=sel, error=str(e))
            continue

    log.error("google_auth.sso_button_not_found")
    return None


async def _handle_account_chooser(gpage: Page, email: str) -> bool:
    """Pick the right account if Google shows the chooser. True if it advanced."""
    try:
        await asyncio.sleep(random.uniform(1.0, 2.0))

        if email:
            by_email = gpage.locator(f"[data-identifier='{email}']")
            if await by_email.count() > 0:
                log.info("google_auth.choosing_account_by_email")
                await by_email.first.click()
                await asyncio.sleep(random.uniform(2.0, 4.0))
                return True

            by_text = gpage.locator(f"div:has-text('{email}')").last
            if await by_text.count() > 0 and await by_text.is_visible():
                log.info("google_auth.choosing_account_by_text")
                await by_text.click()
                await asyncio.sleep(random.uniform(2.0, 4.0))
                return True

        # Single-account chooser with no match — take the first entry.
        first = gpage.locator("[data-authuser], li[class*='account'], div[role='link']").first
        if await first.count() > 0 and await first.is_visible():
            log.info("google_auth.choosing_first_account")
            await first.click()
            await asyncio.sleep(random.uniform(2.0, 4.0))
            return True
    except Exception as e:
        log.debug("google_auth.account_chooser_skipped", error=str(e))
    return False


async def _handle_credential_prompt(gpage: Page, email: str, password: str) -> bool:
    """Fill email/password if Google fell back to a full sign-in form."""
    try:
        email_field = gpage.locator("input[type='email'], #identifierId").first
        if await email_field.count() > 0 and await email_field.is_visible():
            if not email:
                log.error("google_auth.email_prompt_but_no_email_configured")
                return False
            log.info("google_auth.entering_email")
            await email_field.click()
            await email_field.type(email, delay=random.randint(60, 140))
            nxt = gpage.locator("#identifierNext, button:has-text('Next')").first
            if await nxt.count() > 0:
                await nxt.click()
            await asyncio.sleep(random.uniform(2.5, 4.5))

        pwd_field = gpage.locator("input[type='password']").first
        if await pwd_field.count() > 0 and await pwd_field.is_visible():
            if not password:
                log.error(
                    "google_auth.password_required_but_not_configured",
                    hint="set GOOGLE_PASSWORD in .env, or pre-authorise the profile",
                )
                return False
            log.info("google_auth.entering_password")
            await pwd_field.click()
            await pwd_field.type(password, delay=random.randint(60, 140))
            nxt = gpage.locator("#passwordNext, button:has-text('Next')").first
            if await nxt.count() > 0:
                await nxt.click()
            await asyncio.sleep(random.uniform(4.0, 7.0))

        # 2FA cannot be automated — fail clearly instead of hanging.
        body = (await gpage.evaluate("() => document.body.innerText || ''")).lower()
        for marker in ("2-step verification", "verify it's you", "enter the code", "check your phone"):
            if marker in body:
                log.error("google_auth.2fa_challenge_blocked", marker=marker)
                return False

        return True
    except Exception as e:
        log.warning("google_auth.credential_prompt_error", error=str(e))
        return False


async def login_with_google(page: Page, context: BrowserContext) -> bool:
    """
    Complete the Fiverr "Continue with Google" flow.

    Returns True if we end up back on Fiverr authenticated.
    """
    cfg = load_config()
    env = cfg["_env"]
    email = env.get("google_email") or env.get("email") or ""
    password = env.get("google_password") or ""
    base_url = cfg["target"]["base_url"]

    log.info("google_auth.starting", email_configured=bool(email))

    try:
        await page.goto(f"{base_url}/login", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(random.uniform(2.0, 4.0))
        await _dismiss_cookie_banner(page)

        # A challenge can appear on the login page too.
        from ..behavior.challenge import is_challenge_present, solve_human_touch
        if await is_challenge_present(page):
            log.info("google_auth.challenge_on_login_page")
            if not await solve_human_touch(page, context):
                log.error("google_auth.blocked_by_challenge")
                return False
            await asyncio.sleep(random.uniform(2.0, 3.0))

        gpage = await _click_google_button(page)
        if gpage is None:
            return False

        is_popup = gpage is not page

        # Google may go straight through on preserved cookies, or prompt.
        if "google.com" in (gpage.url or ""):
            advanced = await _handle_account_chooser(gpage, email)
            if not advanced:
                if not await _handle_credential_prompt(gpage, email, password):
                    if is_popup and not gpage.is_closed():
                        await gpage.close()
                    return False

        # Wait for the OAuth round-trip to land back on Fiverr.
        deadline = asyncio.get_event_loop().time() + 45
        while asyncio.get_event_loop().time() < deadline:
            if is_popup and gpage.is_closed():
                break
            if "fiverr.com" in (page.url or "") and "/login" not in (page.url or ""):
                break
            await asyncio.sleep(1.0)

        if is_popup and not gpage.is_closed():
            try:
                await gpage.close()
            except Exception:
                pass

        await asyncio.sleep(random.uniform(2.0, 4.0))

        # Land somewhere known-good and confirm.
        try:
            await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(2.0, 3.0))

        from .auth import is_logged_in
        if await is_logged_in(page, context):
            log.info("google_auth.login_success")
            await save_cookies(context)
            return True

        log.error("google_auth.login_failed_not_authenticated", url=page.url[:120])
        return False

    except Exception as e:
        log.error("google_auth.exception", error=str(e))
        return False
