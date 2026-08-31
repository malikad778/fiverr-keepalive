"""
src/behavior/scroll.py
Natural scrolling simulation with physics-based easing,
pause-to-read behavior, and random back-scroll.
"""
import asyncio
import random
import math

from playwright.async_api import Page

from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("scroll")


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_in_out(t: float) -> float:
    return t * t * (3 - 2 * t)


async def scroll_down(
    page: Page,
    total_px: int | None = None,
    duration_ms: int | None = None,
    style: str = "natural",
) -> None:
    """
    Scroll down by total_px pixels over duration_ms milliseconds.
    Simulates realistic scroll behavior with easing.
    """
    if total_px is None:
        total_px = random.randint(300, 900)
    if duration_ms is None:
        duration_ms = random.randint(1200, 3500)

    steps = max(10, duration_ms // 30)
    step_delay = duration_ms / steps / 1000
    scrolled = 0

    for i in range(steps):
        t = (i + 1) / steps
        if style == "natural":
            eased = _ease_in_out(t)
        elif style == "lazy":
            eased = _ease_out_cubic(t)
        else:
            eased = t

        target = int(total_px * eased)
        delta = target - scrolled

        if delta > 0:
            await page.mouse.wheel(0, delta)
            scrolled = target

        # Occasional micro-pause (as if reading)
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(0.3, 1.2))
        else:
            await asyncio.sleep(step_delay * random.uniform(0.85, 1.15))


async def scroll_up(page: Page, total_px: int | None = None) -> None:
    """Scroll back up (as if re-reading content)."""
    if total_px is None:
        total_px = random.randint(100, 400)
    steps = random.randint(8, 20)
    per_step = total_px // steps

    for _ in range(steps):
        await page.mouse.wheel(0, -per_step)
        await asyncio.sleep(random.uniform(0.04, 0.12))


async def natural_page_browse(page: Page, read_time_seconds: float | None = None) -> None:
    """
    Simulate a full human-like page read:
    - Scroll down gradually
    - Pause to 'read' sections
    - Optionally scroll back up
    - Final idle before done
    """
    if read_time_seconds is None:
        read_time_seconds = random.uniform(15, 60)

    cfg_scroll_back = get("behavior.scroll.scroll_back_probability", 0.35)
    cfg_pause       = get("behavior.scroll.pause_on_content_probability", 0.6)

    # Get page height
    try:
        page_height = await page.evaluate("document.body.scrollHeight")
    except Exception:
        page_height = 2000

    # Divide page into sections and scroll through them
    num_sections = random.randint(3, 8)
    section_size = page_height // num_sections
    elapsed = 0.0

    for section_idx in range(num_sections):
        if elapsed >= read_time_seconds:
            break

        scroll_amount = section_size + random.randint(-50, 100)
        scroll_amount = max(100, scroll_amount)

        await scroll_down(page, total_px=scroll_amount)

        # Pause to read
        if random.random() < cfg_pause:
            pause = random.uniform(1.5, 6.0)
            log.debug("scroll.reading_pause", seconds=round(pause, 1))
            await asyncio.sleep(pause)
            elapsed += pause

        # Random scroll back
        if random.random() < cfg_scroll_back:
            back_px = random.randint(50, 200)
            await scroll_up(page, back_px)
            await asyncio.sleep(random.uniform(0.5, 2.0))

        elapsed += random.uniform(1, 3)

    # End idle
    await asyncio.sleep(random.uniform(1, 4))
