"""
src/behavior/simulator.py
Master behavior orchestrator — drives the full session loop:
warm-up → action cycle → idle → ping → repeat
"""
import asyncio
import random
from datetime import datetime, timedelta

from playwright.async_api import Page

from .actions import do_random_action, visit_profile
from .idle import simulate_idle, warm_up_session
from .mouse import random_mouse_drift
from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("simulator")


class BehaviorSimulator:
    """
    Runs the continuous session activity loop.
    Designed to be awaited in a long-running asyncio task.
    """

    def __init__(self, page: Page):
        self._page = page
        self._base_url = get("target.base_url", "https://www.fiverr.com")
        self._ping_interval = get("target.ping_interval_seconds", 480)
        self._session_refresh_hours = get("target.session_refresh_hours", 6)
        self._running = False
        self._last_full_refresh = datetime.utcnow()
        self._cycle_count = 0

    async def run(self) -> None:
        """
        Main loop — never returns unless stopped.
        Cycle: warm-up → actions → idle → sleep → repeat
        """
        self._running = True
        log.info("simulator.starting")

        # Warm up: browse non-profile pages first
        await warm_up_session(self._page, self._base_url)

        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("simulator.cycle_error", error=str(e))
                await asyncio.sleep(30)

        log.info("simulator.stopped")

    async def stop(self) -> None:
        self._running = False

    async def _run_cycle(self) -> None:
        self._cycle_count += 1
        log.info("simulator.cycle_start", cycle=self._cycle_count)

        # 1. Visit profile to confirm online status
        await visit_profile(self._page)

        # 2. Random mouse drift while on profile (reading own stats)
        await random_mouse_drift(self._page, duration_seconds=random.uniform(3, 8))

        # 3. Do a weighted random action (browse, gigs, messages, etc.)
        action = await do_random_action(self._page)
        log.info("simulator.action_done", action=action)

        # 4. Simulate idle time (reading, away from keyboard, etc.)
        idle_secs = random.uniform(
            get("behavior.idle.min_idle_seconds", 30),
            get("behavior.idle.max_idle_seconds", 120),
        )
        await simulate_idle(self._page, duration_seconds=idle_secs)

        # 5. Return to profile to stay "online"
        await visit_profile(self._page)

        # 6. Full session refresh every N hours (re-warm from homepage)
        if datetime.utcnow() - self._last_full_refresh > timedelta(hours=self._session_refresh_hours):
            log.info("simulator.full_session_refresh")
            await warm_up_session(self._page, self._base_url)
            self._last_full_refresh = datetime.utcnow()

        # 7. Sleep until next ping cycle
        sleep_time = self._ping_interval + random.randint(-60, 60)
        log.info("simulator.sleeping", seconds=sleep_time)
        await asyncio.sleep(sleep_time)


class BehaviorSimulator:
    """
    Runs the continuous session activity loop.
    Designed to be awaited in a long-running asyncio task.
    """

    def __init__(self, page: Page):
        self._page = page
        self._base_url = get("target.base_url", "https://www.fiverr.com")
        self._ping_interval = get("target.ping_interval_seconds", 480)
        self._session_refresh_hours = get("target.session_refresh_hours", 6)
        self._running = False
        self._last_full_refresh = datetime.utcnow()
        self._cycle_count = 0

    async def run(self) -> None:
        """Main loop — runs indefinitely until stop() is called."""
        self._running = True
        log.info("simulator.starting")
        await warm_up_session(self._page, self._base_url)

        while self._running:
            try:
                await self._run_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("simulator.cycle_error", error=str(e))
                await asyncio.sleep(30)

        log.info("simulator.stopped")

    async def stop(self) -> None:
        self._running = False

    async def _run_cycle(self) -> None:
        self._cycle_count += 1
        log.info("simulator.cycle_start", cycle=self._cycle_count)

        await visit_profile(self._page)
        await random_mouse_drift(self._page, duration_seconds=random.uniform(3, 8))
        action = await do_random_action(self._page)
        log.info("simulator.action_done", action=action)

        idle_secs = random.uniform(
            get("behavior.idle.min_idle_seconds", 30),
            get("behavior.idle.max_idle_seconds", 120),
        )
        await simulate_idle(self._page, duration_seconds=idle_secs)
        await visit_profile(self._page)

        if datetime.utcnow() - self._last_full_refresh > timedelta(hours=self._session_refresh_hours):
            log.info("simulator.full_session_refresh")
            await warm_up_session(self._page, self._base_url)
            self._last_full_refresh = datetime.utcnow()

        sleep_time = self._ping_interval + random.randint(-60, 60)
        log.info("simulator.sleeping", seconds=sleep_time)
        await asyncio.sleep(sleep_time)
