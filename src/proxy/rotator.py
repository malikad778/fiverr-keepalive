"""
src/proxy/rotator.py
Proxy lifecycle manager — validates proxy health, tracks session time,
and rotates when the sticky session expires.
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

from ..utils.config import get, load_config
from ..utils.logger import get_logger

log = get_logger("proxy.rotator")


class ProxyRotator:
    """
    Manages a residential proxy connection.
    Tracks session age and signals when rotation is needed.

    For Bright Data: sticky sessions are controlled via session ID in username.
    For Oxylabs:    sticky sessions via username suffix -sessid-XXXX.
    """

    def __init__(self):
        cfg = load_config()
        env = cfg["_env"]
        self._host     = env["proxy_host"]
        self._port     = env["proxy_port"]
        self._base_user = env["proxy_user"]
        self._password  = env["proxy_pass"]
        self._provider  = get("proxy.provider", "brightdata")
        self._strategy  = get("proxy.rotation_strategy", "sticky_session")
        self._sticky_minutes = get("proxy.sticky_session_minutes", 30)
        self._session_start: Optional[float] = None
        self._current_session_id: str = self._new_session_id()

    def _new_session_id(self) -> str:
        import random, string
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=10))

    def _build_username(self) -> str:
        """Build provider-specific username with session ID embedded."""
        if self._strategy != "sticky_session":
            return self._base_user

        if self._provider == "brightdata":
            # BrightData format: customer-XXXXX-zone-residential-session-SESSIONID
            return f"{self._base_user}-session-{self._current_session_id}"
        elif self._provider == "oxylabs":
            # Oxylabs format: customer-XXXXX-sessid-SESSIONID
            return f"{self._base_user}-sessid-{self._current_session_id}"
        elif self._provider == "smartproxy":
            # SmartProxy: user-sessid-SESSIONID
            return f"{self._base_user}-sessid-{self._current_session_id}"
        return self._base_user

    def get_proxy_url(self) -> str:
        """Return current proxy URL string."""
        user = _build_username(self) if callable(getattr(self, '_build_username', None)) else self._build_username()
        return f"http://{user}:{self._password}@{self._host}:{self._port}"

    def get_playwright_proxy(self) -> dict:
        """Return proxy dict for Playwright context."""
        return {
            "server":   f"http://{self._host}:{self._port}",
            "username": self._build_username(),
            "password": self._password,
        }

    def should_rotate(self) -> bool:
        """True if sticky session has exceeded max age."""
        if self._strategy != "sticky_session":
            return False
        if self._session_start is None:
            return False
        elapsed_minutes = (time.monotonic() - self._session_start) / 60
        return elapsed_minutes >= self._sticky_minutes

    def rotate(self) -> None:
        """Generate a new session ID (effectively rotating the exit IP)."""
        old = self._current_session_id
        self._current_session_id = self._new_session_id()
        self._session_start = time.monotonic()
        log.info("proxy.rotated", old_session=old, new_session=self._current_session_id)

    def start_session(self) -> None:
        self._session_start = time.monotonic()
        log.info("proxy.session_started", session_id=self._current_session_id)

    async def validate(self) -> dict:
        """
        Test proxy connectivity and return current IP info.
        Returns dict with 'ok', 'ip', 'country'.
        """
        check_url = get("proxy.health_check_url", "https://api.ipify.org")
        proxy_url = f"http://{self._build_username()}:{self._password}@{self._host}:{self._port}"
        try:
            async with httpx.AsyncClient(
                proxies=proxy_url,
                timeout=15.0,
                follow_redirects=True,
            ) as client:
                resp = await client.get(check_url)
                ip = resp.text.strip()

                # Get country info
                geo_resp = await client.get(f"http://ip-api.com/json/{ip}?fields=country,countryCode")
                geo = geo_resp.json() if geo_resp.status_code == 200 else {}

                log.info("proxy.validated", ip=ip, country=geo.get("country", "?"))
                return {"ok": True, "ip": ip, "country": geo.get("country", "?")}
        except Exception as e:
            log.error("proxy.validation_failed", error=str(e))
            return {"ok": False, "ip": None, "country": None}
