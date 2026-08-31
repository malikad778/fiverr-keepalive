"""
src/behavior/mouse.py
Human-like mouse movement using Bezier curves with speed variance
and micro-jitter — indistinguishable from real user input.
"""
import asyncio
import random
import math
from typing import Tuple

from playwright.async_api import Page


def _bezier_point(t: float, points: list[Tuple[float, float]]) -> Tuple[float, float]:
    """De Casteljau algorithm for arbitrary-degree Bezier curve."""
    pts = list(points)
    n = len(pts)
    while n > 1:
        pts = [
            (
                (1 - t) * pts[i][0] + t * pts[i + 1][0],
                (1 - t) * pts[i][1] + t * pts[i + 1][1],
            )
            for i in range(n - 1)
        ]
        n -= 1
    return pts[0]


def _generate_bezier_path(
    start: Tuple[float, float],
    end: Tuple[float, float],
    num_control: int = 6,
    steps: int = 30,
) -> list[Tuple[float, float]]:
    """
    Generate a Bezier path from start to end with random control points.
    The path naturally curves and doesn't move in a straight line.
    """
    sx, sy = start
    ex, ey = end

    # Random control points in the region between start and end
    dx = ex - sx
    dy = ey - sy
    control_pts = [(sx, sy)]
    for i in range(1, num_control):
        t = i / num_control
        # Base position along the straight line
        bx = sx + dx * t
        by = sy + dy * t
        # Add perpendicular random offset
        perp_x = -dy * random.uniform(-0.3, 0.3)
        perp_y =  dx * random.uniform(-0.3, 0.3)
        control_pts.append((bx + perp_x, by + perp_y))
    control_pts.append((ex, ey))

    path = []
    for i in range(steps + 1):
        t = i / steps
        # Ease-in-out timing function
        t_eased = t * t * (3 - 2 * t)
        pt = _bezier_point(t_eased, control_pts)
        path.append(pt)
    return path


async def move_mouse_to(
    page: Page,
    x: float,
    y: float,
    duration_ms: int | None = None,
    micro_jitter: bool = True,
) -> None:
    """
    Move mouse from current position to (x, y) along a Bezier curve.
    duration_ms controls total animation time.
    micro_jitter adds tiny random noise while hovering near the target.
    """
    # Get current mouse position (Playwright doesn't expose it, start from 0,0 or last)
    start_x = random.uniform(100, 900)
    start_y = random.uniform(100, 600)

    if duration_ms is None:
        distance = math.hypot(x - start_x, y - start_y)
        # Realistic: ~400-600 px/sec average
        duration_ms = int(distance / random.uniform(0.4, 0.7))
        duration_ms = max(200, min(duration_ms, 3000))

    path = _generate_bezier_path(
        (start_x, start_y), (x, y),
        num_control=random.randint(4, 8),
        steps=max(10, duration_ms // 20),
    )

    step_delay = duration_ms / len(path) / 1000  # seconds

    for px, py in path:
        # Add micro-jitter at each step
        jx = px + (random.gauss(0, 0.5) if micro_jitter else 0)
        jy = py + (random.gauss(0, 0.5) if micro_jitter else 0)
        await page.mouse.move(jx, jy)
        await asyncio.sleep(step_delay * random.uniform(0.8, 1.2))


async def click_at(
    page: Page,
    x: float,
    y: float,
    button: str = "left",
    hold_ms: int | None = None,
) -> None:
    """Move to target then click with realistic hold time."""
    await move_mouse_to(page, x, y)

    # Small pause before click (humans don't click instantly on arrival)
    await asyncio.sleep(random.uniform(0.05, 0.25))

    hold = hold_ms if hold_ms else random.randint(50, 180)
    await page.mouse.down(button=button)
    await asyncio.sleep(hold / 1000)
    await page.mouse.up(button=button)


async def hover_element(page: Page, selector: str, timeout: int = 5000) -> bool:
    """Move to a page element with human-like mouse movement."""
    try:
        elem = await page.wait_for_selector(selector, timeout=timeout)
        if not elem:
            return False
        bbox = await elem.bounding_box()
        if not bbox:
            return False
        cx = bbox["x"] + bbox["width"] / 2 + random.uniform(-5, 5)
        cy = bbox["y"] + bbox["height"] / 2 + random.uniform(-3, 3)
        await move_mouse_to(page, cx, cy)
        return True
    except Exception:
        return False


async def random_mouse_drift(page: Page, duration_seconds: float = 2.0) -> None:
    """
    Idle mouse drift — move mouse aimlessly as a real user would
    while reading content.
    """
    end = asyncio.get_event_loop().time() + duration_seconds
    while asyncio.get_event_loop().time() < end:
        x = random.uniform(100, 1200)
        y = random.uniform(80, 700)
        await move_mouse_to(page, x, y, duration_ms=random.randint(800, 2000))
        await asyncio.sleep(random.uniform(0.3, 1.5))
