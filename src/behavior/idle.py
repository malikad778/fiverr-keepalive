"""
src/behavior/idle.py
Simulates realistic idle behavior — focus/blur events, typing pauses,
tab activity simulation — to maintain a trusted active session.
"""
import asyncio
import random

from playwright.async_api import Page

from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("idle")


async def simulate_idle(page: Page, duration_seconds: float | None = None) -> None:
    """
    Simulate user being idle (not scrolling/clicking but session is active).
    Fires focus/blur events, tiny mouse moves, and variable pauses.
    """
    if duration_seconds is None:
        cfg = get("behavior.idle", {})
        duration_seconds = random.uniform(
            cfg.get("min_idle_seconds", 30),
            cfg.get("max_idle_seconds", 120),
        )

    log.debug("idle.started", duration=round(duration_seconds, 1))
    end = asyncio.get_event_loop().time() + duration_seconds

    while asyncio.get_event_loop().time() < end:
        action = random.choices(
            ["blur_focus", "tiny_mouse", "wait", "scroll_tiny"],
            weights=[0.15, 0.25, 0.50, 0.10],
        )[0]

        if action == "blur_focus":
            await _simulate_tab_blur(page)
        elif action == "tiny_mouse":
            await _tiny_mouse_move(page)
        elif action == "scroll_tiny":
            await _micro_scroll(page)
        else:
            wait = random.uniform(3, 15)
            await asyncio.sleep(min(wait, end - asyncio.get_event_loop().time()))

    log.debug("idle.finished")


async def _simulate_tab_blur(page: Page) -> None:
    """Simulate user switching to another tab and returning."""
    try:
        blur_duration = random.uniform(2, 12)
        # Fire blur
        await page.evaluate("""
            window.dispatchEvent(new Event('blur'));
            document.dispatchEvent(new Event('visibilitychange'));
            Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
        """)
        await asyncio.sleep(blur_duration)
        # Return focus
        await page.evaluate("""
            Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
            window.dispatchEvent(new Event('focus'));
        """)
        await asyncio.sleep(random.uniform(0.5, 2))
    except Exception as e:
        log.debug("idle.blur_error", error=str(e))


async def _tiny_mouse_move(page: Page) -> None:
    """Tiny random mouse movement (fidgeting)."""
    x = random.uniform(200, 1000)
    y = random.uniform(100, 600)
    await page.mouse.move(x, y)
    await asyncio.sleep(random.uniform(0.1, 0.5))


async def _micro_scroll(page: Page) -> None:
    """Tiny scroll — like accidentally grazing the scroll wheel."""
    delta = random.choice([-3, -2, 2, 3]) * random.randint(1, 5)
    await page.mouse.wheel(0, delta)
    await asyncio.sleep(random.uniform(0.2, 0.8))


async def simulate_typing_pause(duration_seconds: float = 2.0) -> None:
    """
    Simulate the pause a user takes while thinking before typing.
    Just a variable delay with occasional tiny variance.
    """
    t = random.uniform(duration_seconds * 0.6, duration_seconds * 1.4)
    await asyncio.sleep(t)


async def warm_up_session(page: Page, base_url: str) -> None:
    """
    Browse 1-2 non-sensitive pages before navigating to the profile.
    This establishes browsing history context and builds trust.
    """
    warm_up_pages = [
        base_url + "/",
        base_url + "/categories",
        base_url + "/explore",
    ]
    pages_to_visit = random.sample(warm_up_pages, k=random.randint(1, 2))

    for url in pages_to_visit:
        log.info("idle.warmup_visiting", url=url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(3, 8))

            # Small scroll to look natural
            from .scroll import scroll_down
            await scroll_down(page, total_px=random.randint(200, 500))
            await asyncio.sleep(random.uniform(2, 6))
        except Exception as e:
            log.warning("idle.warmup_page_error", url=url, error=str(e))
