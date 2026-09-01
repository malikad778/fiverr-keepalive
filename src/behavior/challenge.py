"""
src/behavior/challenge.py
PerimeterX "It needs a human touch" challenge handling.

Strategy (derived from the observed manual fix):
  1. Detect the block page precisely (never guess).
  2. Locate the real "PRESS & HOLD" control - main frame or nested iframe.
  3. Hold it with human-like micro-tremor until PerimeterX issues clearance.
  4. If that fails: wipe *Fiverr* site data only (cookies, localStorage,
     IndexedDB, service workers, cache) while preserving the Google SSO
     cookies, reload, solve the fresh challenge, then re-login via
     "Continue with Google".

The Fiverr-only scoping matters: a full browser wipe would also destroy the
Google session, which turns the one-click SSO into a password + 2FA prompt
that cannot be satisfied headlessly.
"""
import asyncio
import random
from typing import Optional, Tuple

from playwright.async_api import Page, BrowserContext, Frame

from .mouse import move_mouse_to
from ..session.manager import save_cookies, restore_cookies
from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("challenge")

# Domains whose cookies get wiped on recovery.
FIVERR_COOKIE_DOMAINS = ["fiverr.com"]

# Domains whose cookies are preserved so SSO stays one-click.
SSO_PRESERVE_DOMAINS = ["google.com", "accounts.google.com", "googleusercontent.com"]

# Origins whose storage buckets get cleared via CDP.
FIVERR_ORIGINS = ["https://www.fiverr.com", "https://fiverr.com"]

# Strong markers - any one of these alone means we are blocked.
_STRONG_TEXT = (
    # Full-page block variant.
    "it needs a human touch",
    "errcode pxcr",
    "access to this page has been denied",
    "additional security check is required",
    # Modal variant (window._pxModal), injected over the live page when an
    # XHR is blocked. Completely different copy from the full-page block.
    "before we continue",
    "press & hold to confirm you are",
)
_STRONG_TITLE = (
    "human touch",
    "human verification",      # modal variant's document title
    "access to this page",
    "just a moment",
    "attention required",
)
# Weak marker - only meaningful alongside a strong one or the PX container.
_WEAK_TEXT = ("press & hold", "press and hold")

_PX_CONTAINER_SELECTORS = (
    "#px-captcha",
    "div[id^='px-captcha']",
    "[class*='px-captcha']",
    "#px-captcha-wrapper",
)


async def _body_text(page: Page) -> str:
    try:
        txt = await page.evaluate("() => (document.body && document.body.innerText) || ''")
        return (txt or "").lower()
    except Exception:
        return ""


async def _page_title(page: Page) -> str:
    try:
        return (await page.title() or "").lower()
    except Exception:
        return ""


async def is_challenge_present(page: Page) -> bool:
    """
    True if the page is sitting on a PerimeterX block/challenge screen.

    Deliberately conservative: "press & hold" text on its own is NOT enough,
    since a gig listing could legitimately contain that phrase.
    """
    try:
        title = await _page_title(page)
        if any(m in title for m in _STRONG_TITLE):
            return True

        text = await _body_text(page)
        if any(m in text for m in _STRONG_TEXT):
            return True

        # PX container present in any frame is definitive.
        for frame in page.frames:
            for sel in _PX_CONTAINER_SELECTORS:
                try:
                    if await frame.locator(sel).count() > 0:
                        return True
                except Exception:
                    continue

        # Weak marker only counts on a near-empty page (block pages are sparse).
        if any(m in text for m in _WEAK_TEXT) and len(text) < 1200:
            return True

        return False
    except Exception as e:
        log.debug("challenge.check_error", error=str(e))
        return False


async def _scan_for_target(
    page: Page,
    allow_fallback: bool = False,
) -> Optional[Tuple[Frame, dict]]:
    """
    One pass over every frame looking for the press-and-hold control.

    Two tiers, because #px-captcha is a trap. It is in the markup from the
    very first paint, initially holding the literal text "Loading challenge",
    while the real control renders later as an <iframe> inside
    <template shadowrootmode="closed">. Matching the host straight away means
    pressing a loading placeholder and reporting a failed solve.

    Playwright cannot pierce a closed shadow root, but the browser's frame
    tree still exposes that iframe's document - in production it shows up as
    an `about:blank` child frame containing the aria-label button. So the
    precise tier nearly always wins; the host is a last resort for aiming
    only, and only once we have given the widget time to appear.
    """
    precise = [
        # The matched element IS the button (~310x100 in production).
        "div[aria-label*='Press & Hold' i]",
        "button[aria-label*='Press & Hold' i]",
    ]
    fallback = [
        # Host div - coordinates only. Used if the shadow iframe never
        # surfaces as its own frame.
        "#px-captcha",
        "div[id^='px-captcha']:not(#px-captcha-wrapper)",
    ]
    # Deliberately NOT candidates:
    #   text=/press.*hold/  -> matches div.px-captcha-message, the instruction
    #                          text ("Press & Hold to confirm you are a human"),
    #                          which sits above the button. Pressing it does
    #                          nothing and looks like a failed solve.
    #   #px-captcha-wrapper -> the full-viewport modal overlay.
    #   .px-captcha-background -> full-screen dimmer.
    # All three remain in _PX_CONTAINER_SELECTORS for *detection* only.

    candidates = precise + fallback if allow_fallback else precise

    for sel in candidates:
        for frame in page.frames:
            try:
                loc = frame.locator(sel).first
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    continue
                box = await loc.bounding_box()
                if not box:
                    continue
                # Reject slivers and full-page wrappers.
                if box["width"] < 60 or box["height"] < 28:
                    continue
                if box["height"] > 400 or box["width"] > 900:
                    continue

                # Never press a placeholder. PerimeterX seeds #px-captcha with
                # "Loading challenge", and swaps in "Error. Failed to display
                # challenge." if its own 10s timer expires first.
                if sel in fallback:
                    try:
                        inner = (await loc.inner_text() or "").strip().lower()
                    except Exception:
                        inner = ""
                    if "loading challenge" in inner or "failed to display" in inner:
                        log.info("challenge.host_still_placeholder", text=inner[:60])
                        continue

                press_box = dict(box)
                # #px-captcha is a *host*, not the control: CSS gives it
                # min-height:100px while the interactive iframe inside the
                # closed shadow root is only 52px tall and flush to its top.
                # Aiming at the host's centre can land below the button, so
                # clamp to the top band. The aria-label div needs no clamp -
                # there the matched element is the button itself.
                if sel.startswith(("#px-captcha", "div[id^=")) and box["height"] > 60:
                    press_box["height"] = 52.0

                log.info(
                    "challenge.target_located",
                    selector=sel,
                    frame=frame.url[:80],
                    box=box,
                    press_box=press_box,
                )
                return frame, press_box
            except Exception:
                continue
    return None


async def _find_press_hold_target(
    page: Page,
    timeout: float = 15.0,
) -> Optional[Tuple[Frame, dict]]:
    """
    Locate the press-and-hold control, polling until it renders.

    The widget is injected by js.px-cloud.net *after* the block page loads,
    so a single snapshot taken a second or two in finds nothing - which is
    exactly how this failed in production: detection fired at 04:57:54 and
    the search gave up at 04:57:56, before PerimeterX had drawn the button.

    The control normally lives in an `about:blank` child frame, not the main
    frame. bounding_box() still returns viewport coordinates, so the result
    can be fed straight to page.mouse.

    Returns None if nothing credible appears before the timeout. Callers must
    NOT fall back to guessed coordinates: pressing a random point for several
    seconds on a normal Fiverr page drags or opens whatever is underneath.
    """
    loop = asyncio.get_event_loop()
    start = loop.time()
    deadline = start + timeout
    # Hold the imprecise host selectors back at first so the real control gets
    # a chance to render. PerimeterX gives itself 10s before declaring the
    # challenge undisplayable (failedToDisplayChallenge limit:1e4), so this
    # grace period stays comfortably inside its own budget.
    fallback_after = start + min(8.0, timeout * 0.55)
    scans = 0

    while loop.time() < deadline:
        scans += 1
        found = await _scan_for_target(
            page,
            allow_fallback=loop.time() >= fallback_after,
        )
        if found:
            return found
        await asyncio.sleep(0.5)

    log.warning("challenge.target_not_found", waited_seconds=timeout, scans=scans)
    return None


# PerimeterX's own failure string. Its bundle sets a 10s timer
# (failedToDisplayChallenge) and swaps this in when the captcha widget never
# renders. Critically this is not a race we can win by retrying: it means PX
# declined to issue a challenge to this visitor, so reloading harder only
# adds blocked requests and deepens the reputation hit.
_PX_REFUSED_TEXT = "failed to display challenge"

# Wall-clock deadline before we are allowed to attempt another solve.
_cooldown_until: float = 0.0


def _in_cooldown() -> float:
    """Seconds remaining in the refusal cooldown, or 0."""
    remaining = _cooldown_until - asyncio.get_event_loop().time()
    return remaining if remaining > 0 else 0.0


def _set_cooldown(seconds: float) -> None:
    global _cooldown_until
    _cooldown_until = asyncio.get_event_loop().time() + seconds
    log.warning("challenge.cooldown_engaged", seconds=round(seconds))


def _px_refused(diag: dict) -> bool:
    """True if PX served a block page but refused to render a challenge."""
    if _PX_REFUSED_TEXT in (diag.get("body_head") or ""):
        return True
    return any(
        _PX_REFUSED_TEXT in (c.get("text") or "").lower()
        for c in diag.get("px_containers", [])
    )


async def _describe_block_page(page: Page) -> dict:
    """
    Snapshot why a solve could not proceed.

    Without this, 'target_not_found' is indistinguishable between three very
    different situations: PerimeterX served a block page whose widget never
    rendered, the detector fired on a page that was never a challenge, or the
    widget is present but outside every selector we know.
    """
    info = {"url": (page.url or "")[:120], "frames": len(page.frames)}
    try:
        info["title"] = (await page.title() or "")[:80]
    except Exception:
        info["title"] = "?"

    found = []
    for frame in page.frames:
        for sel in _PX_CONTAINER_SELECTORS:
            try:
                if await frame.locator(sel).count() > 0:
                    entry = {"sel": sel, "frame": frame.url[:60]}
                    try:
                        entry["text"] = (
                            await frame.locator(sel).first.inner_text() or ""
                        ).strip()[:60]
                    except Exception:
                        pass
                    found.append(entry)
            except Exception:
                continue
    info["px_containers"] = found
    info["has_px_widget"] = bool(found)

    try:
        body = await _body_text(page)
        info["body_head"] = body[:160]
        info["body_len"] = len(body)
    except Exception:
        pass
    return info


async def _still_blocked(page: Page) -> bool:
    """Cheap re-check used while holding - avoids full frame enumeration."""
    title = await _page_title(page)
    if any(m in title for m in _STRONG_TITLE):
        return True
    text = await _body_text(page)
    return any(m in text for m in _STRONG_TEXT)


async def solve_human_touch(
    page: Page,
    context: Optional[BrowserContext] = None,
    max_attempts: int = 3,
) -> bool:
    """
    Press and hold the PerimeterX button until clearance is issued.

    Holds until the challenge actually clears rather than for a fixed
    duration - releasing before the progress ring completes fails the check.
    """
    # Every caller must respect the refusal cooldown, not just handle_challenge -
    # the login paths call this directly and would otherwise keep poking an
    # already-escalated visitor.
    remaining = _in_cooldown()
    if remaining:
        log.warning("challenge.solve_skipped_in_cooldown",
                    seconds_remaining=round(remaining))
        return False

    for attempt in range(1, max_attempts + 1):
        log.info("challenge.solve_attempt", attempt=attempt, max=max_attempts)

        # Let the challenge widget finish rendering.
        await asyncio.sleep(random.uniform(1.5, 3.0))

        target = await _find_press_hold_target(page)
        if not target:
            diag = await _describe_block_page(page)
            log.warning("challenge.no_target", attempt=attempt, **diag)

            # Not a render race - PX is refusing to serve a challenge at all.
            # Back off hard instead of reloading; more requests make it worse.
            if _px_refused(diag):
                log.error(
                    "challenge.px_refused_to_serve",
                    attempt=attempt,
                    hint="PerimeterX declined to issue a challenge to this "
                         "visitor; retrying cannot help, cooling down",
                )
                _set_cooldown(random.uniform(900, 1800))
                return False

            # PerimeterX gives up on its own widget after 10s
            # (failedToDisplayChallenge) and swaps in an error string. A
            # reload is the only way forward - previously this returned
            # immediately, so a block page whose widget failed to paint went
            # straight to a full cookie wipe every single time.
            if attempt < max_attempts:
                log.info("challenge.reloading_before_retry", attempt=attempt)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    log.warning("challenge.reload_failed", error=str(e))
                await asyncio.sleep(random.uniform(3.0, 5.0))

                if not await is_challenge_present(page):
                    log.info("challenge.cleared_by_reload", attempt=attempt)
                    return True
                continue

            log.error("challenge.no_target_giving_up", attempt=attempt)
            return False

        _frame, box = target

        # Aim near the middle, with a small human offset - but stay well
        # inside the control so the press always lands on it.
        cx = box["x"] + box["width"] / 2 + random.uniform(-box["width"] * 0.12, box["width"] * 0.12)
        cy = box["y"] + box["height"] / 2 + random.uniform(-box["height"] * 0.15, box["height"] * 0.15)

        try:
            log.info("challenge.moving_to_button", x=round(cx), y=round(cy))
            await move_mouse_to(page, cx, cy)
            await asyncio.sleep(random.uniform(0.25, 0.65))

            log.info("challenge.press_start")
            await page.mouse.down()

            max_hold = 20.0
            poll = 0.3
            loop = asyncio.get_event_loop()
            start = loop.time()
            cleared = False

            try:
                while loop.time() - start < max_hold:
                    # Hand tremor while holding - sub-pixel, not a drag.
                    await page.mouse.move(
                        cx + random.gauss(0, 0.6),
                        cy + random.gauss(0, 0.6),
                    )
                    await asyncio.sleep(poll)

                    try:
                        if not await _still_blocked(page):
                            cleared = True
                            held = round(loop.time() - start, 1)
                            log.info("challenge.cleared_during_hold", held_seconds=held)
                            break
                    except Exception:
                        # Navigation mid-hold usually means it worked.
                        pass
            finally:
                await page.mouse.up()
                log.info("challenge.press_released", cleared=cleared)

            # Let the redirect / success animation settle.
            await asyncio.sleep(random.uniform(3.0, 5.0))

            if not await is_challenge_present(page):
                log.info("challenge.solved", attempt=attempt)
                if context:
                    await save_cookies(context)
                    log.info("challenge.clearance_cookies_saved")
                return True

            log.warning("challenge.attempt_failed_still_blocked", attempt=attempt)
            await asyncio.sleep(random.uniform(2.0, 4.0))

        except Exception as e:
            log.error("challenge.solve_error", attempt=attempt, error=str(e))
            try:
                await page.mouse.up()
            except Exception:
                pass

    log.error("challenge.all_solve_attempts_failed")
    return False


async def clear_site_data(
    context: BrowserContext,
    page: Page,
    preserve_domains: Optional[list] = None,
) -> None:
    """
    Wipe Fiverr site data while preserving SSO cookies.

    Cookies are handled by read → filter → clear-all → re-add rather than
    Playwright's domain filter, because stored cookie domains vary
    (".fiverr.com", "www.fiverr.com") and this is exact either way.
    """
    preserve = preserve_domains if preserve_domains is not None else SSO_PRESERVE_DOMAINS
    from ..session.manager import _domain_matches

    # 1. Cookies - drop Fiverr, keep everything else.
    try:
        all_cookies = await context.cookies()
        keep = [
            c for c in all_cookies
            if not _domain_matches(c.get("domain", ""), FIVERR_COOKIE_DOMAINS)
        ]
        dropped = len(all_cookies) - len(keep)
        await context.clear_cookies()
        if keep:
            await context.add_cookies(keep)
        log.info("challenge.cookies_wiped", dropped=dropped, preserved=len(keep))
    except Exception as e:
        log.warning("challenge.cookie_wipe_error", error=str(e))

    # 2. Origin storage buckets (localStorage, IndexedDB, SW, cache) via CDP.
    try:
        cdp = await context.new_cdp_session(page)
        try:
            storage_types = (
                "local_storage,indexeddb,service_workers,"
                "cache_storage,websql,file_systems"
            )
            for origin in FIVERR_ORIGINS:
                try:
                    await cdp.send("Storage.clearDataForOrigin", {
                        "origin": origin,
                        "storageTypes": storage_types,
                    })
                except Exception as e:
                    log.debug("challenge.origin_clear_skipped", origin=origin, error=str(e))
            await cdp.send("Network.clearBrowserCache")
            log.info("challenge.storage_and_cache_cleared", origins=FIVERR_ORIGINS)
        finally:
            await cdp.detach()
    except Exception as e:
        log.warning("challenge.cdp_clear_error", error=str(e))

    # 3. Belt-and-braces: clear web storage from the page if it is on-origin.
    try:
        if "fiverr.com" in (page.url or ""):
            await page.evaluate(
                "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
            )
    except Exception:
        pass

    log.info("challenge.site_data_cleared", preserved_domains=preserve)


async def _page_usable(page: Page) -> bool:
    """
    True if the page is still attached to a live browser.

    Needed because is_challenge_present() deliberately fails open — it returns
    False when it cannot inspect the page, so a transient error doesn't spin
    the solver. That makes a *closed* browser look identical to a cleared
    challenge, and recovery would report success over a dead context
    (observed: soft_recovery_cleared immediately after
    "Target page, context or browser has been closed").
    """
    try:
        if page.is_closed():
            return False
        await page.evaluate("() => 1")
        return True
    except Exception:
        return False


async def soft_recovery(page: Page, context: BrowserContext) -> bool:
    """
    Non-destructive recovery: reload and re-attempt the solve, backing off.

    Keeps the session cookies intact. This is the right default whenever we
    cannot re-authenticate unattended - wiping would trade a challenged but
    still-logged-in session for a logged-out one that only a human with the
    MFA code can restore.
    """
    log.info("challenge.soft_recovery_start")

    for attempt in range(1, 4):
        if _in_cooldown():
            log.warning("challenge.soft_recovery_halted_cooldown",
                        seconds_remaining=round(_in_cooldown()))
            return False

        backoff = random.uniform(8, 20) * attempt
        log.info("challenge.soft_recovery_attempt", attempt=attempt, backoff=round(backoff))
        await asyncio.sleep(backoff)

        try:
            await page.reload(wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.warning("challenge.soft_recovery_reload_failed", error=str(e))
        await asyncio.sleep(random.uniform(3, 6))

        # A dead browser must never be mistaken for a cleared challenge. The
        # page can be gone here for legitimate reasons — a browser recycle or
        # SIGTERM landing mid-recovery — and without this guard the caller is
        # told the session recovered and starts driving a closed context.
        if not await _page_usable(page):
            log.warning("challenge.soft_recovery_page_dead", attempt=attempt)
            return False

        if not await is_challenge_present(page):
            log.info("challenge.soft_recovery_cleared", attempt=attempt)
            try:
                await save_cookies(context)
            except Exception:
                pass
            return True

        if await solve_human_touch(page, context):
            log.info("challenge.soft_recovery_solved", attempt=attempt)
            return True

    log.error("challenge.soft_recovery_exhausted",
              hint="session left intact; a human may need to re-capture cookies")
    return False


async def recover_session_after_challenge(
    page: Page,
    context: BrowserContext,
) -> bool:
    """
    Full recovery, mirroring the fix that worked manually:

      wipe Fiverr data (keep Google) → reload → solve the fresh challenge
      → re-login via "Continue with Google" → save cookies

    Returns True only if the session is usable afterwards.
    """
    # The wipe is only worth doing if we can actually log back in afterwards.
    # With MFA on the account there is no unattended re-auth path, so wiping
    # destroys a recoverable session and forces a manual cookie re-capture.
    # Opt in via session.allow_cookie_wipe once/if automated login works.
    if not get("session.allow_cookie_wipe", False):
        log.warning(
            "challenge.destructive_recovery_disabled",
            reason="session.allow_cookie_wipe=false; wiping would log us out "
                   "with no unattended way back in (MFA)",
        )
        return await soft_recovery(page, context)

    log.info("challenge.recovery_start")

    base_url = "https://www.fiverr.com"

    # 1. Wipe Fiverr state, keep the Google session alive.
    await clear_site_data(context, page)

    # 2. Put the Google cookies back from storage too, in case the live
    #    context never had them (fresh browser, restored profile, etc).
    try:
        await restore_cookies(context, only_domains=SSO_PRESERVE_DOMAINS)
    except Exception as e:
        log.warning("challenge.sso_cookie_restore_error", error=str(e))

    await asyncio.sleep(random.uniform(1.0, 2.5))

    # 3. Reload clean. A fresh challenge here is expected and solvable.
    try:
        await page.goto(base_url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        log.warning("challenge.recovery_nav_error", error=str(e))
    await asyncio.sleep(random.uniform(2.0, 4.0))

    # 4. Solve whatever challenge the clean load produced.
    if await is_challenge_present(page):
        log.info("challenge.recovery_solving_fresh_challenge")
        if not await solve_human_touch(page, context):
            log.error("challenge.recovery_failed_unsolved")
            return False
        await asyncio.sleep(random.uniform(2.0, 4.0))

    # 5. The wipe logged us out of Fiverr - re-authenticate via Google.
    from ..session.auth import is_logged_in
    from ..session.google_auth import login_with_google

    if await is_logged_in(page, context):
        log.info("challenge.recovery_still_authenticated")
        await save_cookies(context)
        return True

    log.info("challenge.recovery_logging_in_with_google")
    if not await login_with_google(page, context):
        log.error("challenge.recovery_google_login_failed")
        return False

    if await is_logged_in(page, context):
        await save_cookies(context)
        log.info("challenge.recovery_complete")
        return True

    log.error("challenge.recovery_login_unverified")
    return False


# PerimeterX's own exoneration window (EXONERATION_EXPIRATION = 9e5 ms in the
# block page's bundle). Re-hitting the block page inside it makes PX log a
# REPEAT_CHALLENGE_SOLVE warning against the visitor id, so repeatedly
# grinding solves is itself a bot signal.
_EXONERATION_SECONDS = 900.0
_REPEAT_SOLVE_LIMIT = 3

_solve_history: list = []


def _record_solve() -> int:
    """Log a solve and return how many happened inside the exoneration window."""
    now = asyncio.get_event_loop().time()
    _solve_history.append(now)
    cutoff = now - _EXONERATION_SECONDS
    _solve_history[:] = [t for t in _solve_history if t >= cutoff]
    return len(_solve_history)


async def handle_challenge(page: Page, context: Optional[BrowserContext] = None) -> bool:
    """
    Single entry point for the behaviour loop.

    Tries the cheap in-place solve first; escalates to the full wipe +
    re-login only if that fails, or if we have already solved several times
    inside PerimeterX's 15-minute repeat-solve window - at that point another
    press-and-hold just deepens the suspicion, and a clean identity is the
    better move. Returns True if the page is usable.
    """
    if not await is_challenge_present(page):
        return True

    log.info("challenge.detected", url=page.url)
    ctx = context or page.context

    # Respect an active refusal cooldown - attempting again inside it just
    # generates more blocked requests against an already-escalated visitor.
    remaining = _in_cooldown()
    if remaining:
        log.warning("challenge.skipped_in_cooldown", seconds_remaining=round(remaining))
        return False

    recent = _record_solve()
    if recent > _REPEAT_SOLVE_LIMIT:
        log.warning(
            "challenge.repeat_solve_limit_hit",
            solves_in_window=recent,
            window_seconds=_EXONERATION_SECONDS,
        )
        return await recover_session_after_challenge(page, ctx)

    if await solve_human_touch(page, ctx):
        return True

    log.warning("challenge.escalating_to_full_recovery")
    return await recover_session_after_challenge(page, ctx)
