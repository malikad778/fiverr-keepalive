"""
src/behavior/simulator.py
Master behavior orchestrator - drives the full session loop:
warm-up → action cycle → idle → ping → repeat
Integrates automated PerimeterX challenge resolution and session recovery.
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from playwright.async_api import Page

from .actions import do_random_action, visit_profile
from .idle import simulate_idle, warm_up_session
from .mouse import random_mouse_drift
from .challenge import handle_challenge
from ..session.manager import save_cookies
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
        self._ping_interval = get("target.ping_interval_seconds", 300)
        self._session_refresh_hours = get("target.session_refresh_hours", 6)
        self._running = False
        self._last_full_refresh = datetime.now(timezone.utc)
        self._cycle_count = 0
        # The loop owns its task so stop() can actually cancel it. Restarting
        # by calling __init__() again and spawning a second run() left the
        # original coroutine alive - it re-read _running, saw True, and kept
        # going, so every unhealthy check doubled the number of live loops
        # driving the same page.
        self._task: Optional[asyncio.Task] = None
        self._loop_active = False

    async def start(self) -> Optional[asyncio.Task]:
        """Start the cycle loop as an owned task. Safe to call repeatedly."""
        if self._task is not None and not self._task.done():
            log.warning("simulator.start_ignored_already_running")
            return self._task
        self._task = asyncio.create_task(self.run())
        return self._task

    async def _check_and_handle_challenge(self) -> bool:
        """
        Solve any challenge blocking the page, escalating to a full
        wipe + Google re-login if the in-place press-and-hold fails.
        """
        try:
            return await handle_challenge(self._page, self._page.context)
        except Exception as e:
            log.warning("simulator.challenge_check_error", error=str(e))
            return False

    async def run(self) -> None:
        """
        Main loop - never returns unless stopped.
        Cycle: warm-up → actions → idle → sleep → repeat
        """
        # Hard guard: never let two loops drive the same page.
        if self._loop_active:
            log.error("simulator.duplicate_run_rejected")
            return

        self._loop_active = True
        self._running = True
        log.info("simulator.starting")

        try:
            # Warm up: browse non-profile pages first
            await warm_up_session(self._page, self._base_url)
            await self._check_and_handle_challenge()

            while self._running:
                try:
                    await self._run_cycle()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.error("simulator.cycle_error", error=str(e))
                    await asyncio.sleep(30)
        finally:
            self._loop_active = False
            log.info("simulator.stopped", cycles=self._cycle_count)

    async def stop(self, timeout: float = 30.0) -> None:
        """
        Stop the loop and *wait* for it to actually exit.

        Clearing the flag alone is not enough: a cycle is usually parked in
        the inter-cycle sleep or a navigation, so it would keep running for
        minutes afterwards - concurrently with whatever recovery the caller
        started next.
        """
        self._running = False
        task, self._task = self._task, None

        if task is None or task.done():
            return

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            log.warning("simulator.stop_error", error=str(e))

    async def _run_cycle(self) -> None:
        self._cycle_count += 1
        log.info("simulator.cycle_start", cycle=self._cycle_count)

        # 1. Check and solve challenge if present
        await self._check_and_handle_challenge()

        # 2. Visit dashboard and profile to confirm online status
        await visit_profile(self._page)
        await self._check_and_handle_challenge()

        # 3. Random mouse drift while on profile (reading own stats)
        await random_mouse_drift(self._page, duration_seconds=random.uniform(3, 8))

        # 4. Do a weighted random action (browse, gigs, messages, etc.)
        action = await do_random_action(self._page)
        log.info("simulator.action_done", action=action)
        await self._check_and_handle_challenge()

        # 5. Simulate idle time (reading, away from keyboard, etc.)
        idle_secs = random.uniform(
            get("behavior.idle.min_idle_seconds", 30),
            get("behavior.idle.max_idle_seconds", 120),
        )
        await simulate_idle(self._page, duration_seconds=idle_secs)

        # 6. Return to profile to maintain active state
        await visit_profile(self._page)
        await self._check_and_handle_challenge()

        # 7. Full session refresh every N hours (re-warm from homepage)
        if datetime.now(timezone.utc) - self._last_full_refresh > timedelta(hours=self._session_refresh_hours):
            log.info("simulator.full_session_refresh")
            await warm_up_session(self._page, self._base_url)
            self._last_full_refresh = datetime.now(timezone.utc)

        # 8. Sleep until next ping cycle.
        # Proportional jitter, not a fixed +/-2s: a 300s interval that only
        # ever varies by two seconds is near-perfect periodicity, which is
        # itself a strong automation signal regardless of how human the
        # individual actions look.
        # 9. Persist the freshest cookies *before* sleeping. Fiverr rotates
        #    session cookies as you browse, so the stored copy drifts out of
        #    date; load_cookies() also rejects anything older than 7 days.
        #    Saving here (not after the sleep) means the snapshot survives a
        #    stop/restart, which almost always lands mid-sleep.
        try:
            await save_cookies(self._page.context)
        except Exception as e:
            log.warning("simulator.cookie_refresh_failed", error=str(e))

        sleep_time = max(60, int(self._ping_interval * random.uniform(0.7, 1.35)))
        log.info("simulator.sleeping", seconds=sleep_time)
        await asyncio.sleep(sleep_time)
