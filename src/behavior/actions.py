"""
src/behavior/actions.py
High-level Fiverr-specific actions that simulate real user engagement:
browsing categories, viewing gigs, checking messages/notifications.
"""
import asyncio
import random

from playwright.async_api import Page

from .mouse import hover_element, move_mouse_to, click_at
from .scroll import natural_page_browse, scroll_down
from .idle import simulate_idle
from ..utils.config import get, load_config
from ..utils.logger import get_logger

log = get_logger("actions")


async def visit_profile(page: Page) -> bool:
    """Navigate to the user's dashboard and profile page."""
    cfg = load_config()
    base_url = cfg["target"].get("base_url", "https://www.fiverr.com")
    profile_url = cfg["target"].get("profile_url", "")
    
    # 1. Visit seller dashboard (crucial for seller online presence)
    dashboard_url = f"{base_url}/dashboard"
    try:
        log.info("actions.visiting_dashboard", url=dashboard_url)
        await page.goto(dashboard_url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(random.uniform(3, 8))
        await natural_page_browse(page, read_time_seconds=random.uniform(10, 20))
    except Exception as e:
        log.warning("actions.visit_dashboard_failed", error=str(e))

    # 2. Visit public profile
    if not profile_url:
        return True
    try:
        log.info("actions.visiting_profile", url=profile_url)
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(random.uniform(2, 5))
        await natural_page_browse(page, read_time_seconds=random.uniform(10, 20))
        return True
    except Exception as e:
        log.warning("actions.visit_profile_failed", error=str(e))
        return False


async def browse_explore(page: Page) -> bool:
    """Browse the Explore/Discover section - looks like natural curiosity."""
    base = get("target.base_url", "https://www.fiverr.com")
    urls = [
        f"{base}/categories",
        f"{base}/explore",
        f"{base}/search/gigs?query=python+developer",
        f"{base}/search/gigs?query=web+development",
        f"{base}/categories/programming-tech",
    ]
    url = random.choice(urls)
    try:
        log.info("actions.browse_explore", url=url)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(1, 3))
        await natural_page_browse(page, read_time_seconds=random.uniform(15, 45))
        return True
    except Exception as e:
        log.warning("actions.browse_explore_failed", error=str(e))
        return False


async def view_random_gig(page: Page) -> bool:
    """Click on a random gig from the current page to deepen engagement."""
    try:
        # Find gig cards
        gig_selectors = [
            "a[href*='/gigs/']",
            ".gig-card-layout a",
            "[data-testid='gig-card'] a",
            ".basic-gig-card a",
        ]
        links = []
        for sel in gig_selectors:
            found = await page.query_selector_all(sel)
            if found:
                links = found
                break

        if not links:
            log.debug("actions.no_gigs_found_on_page")
            return False

        link = random.choice(links[:10])  # pick from top 10
        href = await link.get_attribute("href")
        if href:
            base = get("target.base_url", "https://www.fiverr.com")
            full_url = href if href.startswith("http") else base + href
            log.info("actions.viewing_gig", url=full_url)

            # Hover over card first
            bbox = await link.bounding_box()
            if bbox:
                cx = bbox["x"] + bbox["width"] / 2
                cy = bbox["y"] + bbox["height"] / 2
                await move_mouse_to(page, cx, cy)
                await asyncio.sleep(random.uniform(0.5, 1.5))

            await page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 4))
            await natural_page_browse(page, read_time_seconds=random.uniform(20, 60))
            return True
    except Exception as e:
        log.warning("actions.view_gig_failed", error=str(e))
    return False


async def check_messages(page: Page) -> bool:
    """Visit the inbox - signals active user engagement."""
    base = get("target.base_url", "https://www.fiverr.com")
    try:
        log.info("actions.checking_messages")
        await page.goto(f"{base}/inbox", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(3, 8))
        await scroll_down(page, total_px=random.randint(100, 300))
        await asyncio.sleep(random.uniform(2, 5))
        return True
    except Exception as e:
        log.warning("actions.check_messages_failed", error=str(e))
        return False


async def check_notifications(page: Page) -> bool:
    """Click the notifications bell - common real-user action."""
    try:
        log.info("actions.checking_notifications")
        selectors = [
            "[data-testid='notification-bell']",
            ".notification-bell",
            "a[href*='/notifications']",
            "#notification-bell",
        ]
        for sel in selectors:
            if await hover_element(page, sel, timeout=3000):
                await asyncio.sleep(random.uniform(0.5, 1.5))
                elem = await page.query_selector(sel)
                if elem:
                    await elem.click()
                    await asyncio.sleep(random.uniform(2, 5))
                    return True
    except Exception as e:
        log.warning("actions.notifications_failed", error=str(e))
    return False


async def do_random_action(page: Page) -> str:
    """
    Pick and execute a weighted random action.
    Returns the action name that was performed.
    """
    weights_cfg = get("behavior.actions", {})
    actions = [
        ("browse_explore",       weights_cfg.get("browse_explore_probability", 0.40)),
        ("view_gig",             weights_cfg.get("view_gig_probability",        0.35)),
        ("check_messages",       weights_cfg.get("check_messages_probability",  0.15)),
        ("check_notifications",  weights_cfg.get("check_notifications_probability", 0.10)),
    ]
    names, weights = zip(*actions)
    chosen = random.choices(names, weights=weights)[0]

    log.info("actions.chosen", action=chosen)

    if chosen == "browse_explore":
        await browse_explore(page)
    elif chosen == "view_gig":
        # Go to explore first if needed to get gig links
        await browse_explore(page)
        await view_random_gig(page)
    elif chosen == "check_messages":
        await check_messages(page)
    elif chosen == "check_notifications":
        await visit_profile(page)
        await check_notifications(page)

    return chosen
