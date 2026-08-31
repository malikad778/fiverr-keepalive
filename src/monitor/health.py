"""
src/monitor/health.py
Session health checker — detects login loss, error pages,
challenge pages, and reports to CloudWatch / logs.
"""
import asyncio
import json
from datetime import datetime
from typing import Optional

from playwright.async_api import Page

from ..utils.config import get, load_config
from ..utils.logger import get_logger

log = get_logger("monitor.health")


# Patterns that indicate the session is being challenged
_CHALLENGE_INDICATORS = [
    "verify you're human",
    "are you a robot",
    "checking your browser",
    "captcha",
    "access denied",
    "403 forbidden",
    "429 too many requests",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "just a moment",           # Cloudflare
    "ddos-guard",
]

_LOGIN_INDICATORS = [
    "sign in",
    "log in",
    "login",
    "join fiverr",
]


class HealthMonitor:
    """
    Monitors session health on each cycle. Can detect:
    - Challenge / verification pages
    - Logged-out state
    - HTTP errors
    - Page load failures
    """

    def __init__(self):
        self._base_url = get("target.base_url", "https://www.fiverr.com")
        self._cloudwatch_enabled = get("monitor.cloudwatch_enabled", False)
        self._check_interval = get("monitor.check_interval_seconds", 600)
        self._cw_client = None
        if self._cloudwatch_enabled:
            self._init_cloudwatch()

    def _init_cloudwatch(self):
        try:
            import boto3
            cfg = load_config()
            self._cw_client = boto3.client(
                "logs",
                region_name=cfg["_env"]["aws_region"],
            )
            log.info("monitor.cloudwatch_enabled")
        except Exception as e:
            log.warning("monitor.cloudwatch_init_failed", error=str(e))

    async def check_page(self, page: Page) -> dict:
        """
        Inspect the current page for health indicators.
        Returns a dict with keys: ok, status, issue
        """
        result = {"ok": True, "status": "healthy", "issue": None, "url": page.url}

        try:
            # Get page text content
            content = await page.evaluate("document.body.innerText.toLowerCase()")
            title   = await page.title()
            url     = page.url

            # Check for challenge page
            for indicator in _CHALLENGE_INDICATORS:
                if indicator in content or indicator in title.lower():
                    result.update({
                        "ok":     False,
                        "status": "challenge_detected",
                        "issue":  f"indicator: '{indicator}'",
                    })
                    log.warning(
                        "monitor.challenge_detected",
                        indicator=indicator,
                        url=url,
                        title=title,
                    )
                    await self._emit_metric("ChallengeDetected", 1)
                    return result

            # Check for logged-out state
            if "fiverr.com/login" in url or "fiverr.com/join" in url:
                result.update({
                    "ok":     False,
                    "status": "logged_out",
                    "issue":  f"redirected to {url}",
                })
                log.warning("monitor.logged_out", url=url)
                await self._emit_metric("LoggedOut", 1)
                return result

            for indicator in _LOGIN_INDICATORS:
                if indicator in title.lower():
                    if "fiverr" in title.lower():  # avoid false positives
                        result.update({
                            "ok":     False,
                            "status": "logged_out",
                            "issue":  f"login page title: '{title}'",
                        })
                        return result

            # All good
            await self._emit_metric("SessionHealthy", 1)

        except Exception as e:
            result.update({
                "ok":     False,
                "status": "check_error",
                "issue":  str(e),
            })
            log.error("monitor.check_error", error=str(e))

        return result

    async def _emit_metric(self, metric_name: str, value: float) -> None:
        """Send a custom metric to CloudWatch if enabled."""
        if not self._cloudwatch_enabled or not self._cw_client:
            return
        try:
            log_group = get("monitor.cloudwatch_log_group", "fiverr-keepalive")
            self._cw_client.put_metric_data(
                Namespace=log_group,
                MetricData=[{
                    "MetricName": metric_name,
                    "Value":      value,
                    "Unit":       "Count",
                    "Timestamp":  datetime.utcnow(),
                }],
            )
        except Exception as e:
            log.debug("monitor.cloudwatch_emit_failed", error=str(e))

    async def wait_for_challenge_clear(
        self, page: Page, timeout_seconds: int = 120
    ) -> bool:
        """
        Wait for a challenge page to clear itself (some auto-resolve).
        Returns True if cleared, False if timed out.
        """
        log.info("monitor.waiting_for_challenge_clear", timeout=timeout_seconds)
        end = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < end:
            await asyncio.sleep(5)
            result = await self.check_page(page)
            if result["ok"]:
                log.info("monitor.challenge_cleared")
                return True
        log.error("monitor.challenge_not_cleared", timeout=timeout_seconds)
        return False
