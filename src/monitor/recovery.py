"""
src/monitor/recovery.py
Auto-recovery logic — handles session expiry, challenge pages,
and browser crashes with exponential backoff.
"""
import asyncio
import time
from typing import Optional

from playwright.async_api import Page, BrowserContext

from .health import HealthMonitor
from ..session.auth import ensure_logged_in, is_logged_in
from ..session.manager import save_cookies, clear_session
from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("monitor.recovery")


class RecoveryManager:
    """
    Handles all failure scenarios and attempts to restore a healthy session.
    Uses exponential backoff between retry attempts.
    """

    def __init__(self, health: HealthMonitor):
        self._health = health
        self._backoff = get("recovery.backoff_seconds", [30, 60, 120, 300, 600])
        self._max_attempts = get("session.max_recovery_attempts", 3)
        self._crash_count = 0
        self._crash_window_start = time.monotonic()

    async def handle_unhealthy(
        self,
        page: Page,
        context: BrowserContext,
        health_result: dict,
    ) -> bool:
        """
        Respond to a detected health issue.
        Returns True if recovery succeeded, False if it failed.
        """
        status = health_result.get("status", "unknown")
        log.warning("recovery.handling", status=status, issue=health_result.get("issue"))

        if status == "challenge_detected":
            return await self._recover_from_challenge(page, context)
        elif status == "logged_out":
            return await self._recover_session(page, context)
        elif status == "check_error":
            return await self._recover_crash(page, context)
        else:
            log.error("recovery.unknown_status", status=status)
            return False

    async def _recover_from_challenge(
        self, page: Page, context: BrowserContext
    ) -> bool:
        """Wait for challenge to auto-clear, then verify session."""
        log.info("recovery.challenge_wait")

        # First: just wait — many challenges auto-resolve
        cleared = await self._health.wait_for_challenge_clear(page, timeout_seconds=90)
        if cleared:
            if await is_logged_in(page):
                await save_cookies(context)
                return True

        # If not cleared: reload from home
        log.info("recovery.reloading_from_home")
        base_url = get("target.base_url", "https://www.fiverr.com")
        try:
            await asyncio.sleep(10)
            await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            health = await self._health.check_page(page)
            if health["ok"]:
                return True
        except Exception as e:
            log.error("recovery.reload_failed", error=str(e))

        # Last resort: full re-login
        return await self._recover_session(page, context)

    async def _recover_session(
        self, page: Page, context: BrowserContext
    ) -> bool:
        """Re-establish a logged-in session."""
        log.info("recovery.re_establishing_session")
        await clear_session()

        for attempt in range(1, self._max_attempts + 1):
            backoff = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
            log.info("recovery.session_attempt", attempt=attempt, backoff=backoff)
            await asyncio.sleep(backoff)

            try:
                success = await ensure_logged_in(page, context)
                if success:
                    log.info("recovery.session_restored", attempt=attempt)
                    return True
            except Exception as e:
                log.error("recovery.session_attempt_error", error=str(e))

        log.error("recovery.session_all_attempts_failed")
        return False

    async def _recover_crash(
        self, page: Optional[Page], context: Optional[BrowserContext]
    ) -> bool:
        """Handle a browser crash or page error."""
        self._crash_count += 1
        elapsed = (time.monotonic() - self._crash_window_start) / 3600
        if elapsed < 1:
            max_crashes = get("recovery.max_crashes_per_hour", 5)
            if self._crash_count > max_crashes:
                log.error(
                    "recovery.too_many_crashes",
                    count=self._crash_count,
                    window_hours=1,
                )
                return False
        else:
            self._crash_count = 1
            self._crash_window_start = time.monotonic()

        backoff = self._backoff[min(self._crash_count - 1, len(self._backoff) - 1)]
        log.info("recovery.crash_backoff", seconds=backoff, crash_count=self._crash_count)
        await asyncio.sleep(backoff)
        return True  # Signal caller to restart browser
