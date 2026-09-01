"""
src/browser/engine.py
Playwright browser factory - creates stealth-patched, proxy-aware
browser contexts with persistent profile directories.
"""
import os
import random
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Playwright,
)

from .stealth import StealthPatcher
from ..utils.config import get, load_config
from ..utils.logger import get_logger

log = get_logger("engine")

_ROOT = Path(__file__).resolve().parents[2]


class BrowserEngine:
    """
    Manages browser lifecycle: launch, context creation, stealth patching.
    Supports persistent Chrome profile for session continuity.
    """

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._stealth = StealthPatcher()
        cfg = load_config()
        self._cfg = cfg

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> BrowserContext:
        """Launch browser and return a stealth-patched context."""
        self._playwright = await async_playwright().start()
        launch_args = self._build_launch_args()
        proxy_settings = self._build_proxy()

        fp = self._stealth.fingerprint
        resolution = fp["resolution"]

        log.info(
            "engine.launching",
            headless=get("browser.headless", True),
            proxy=bool(proxy_settings),
            resolution=resolution,
        )

        # Use persistent context so cookies + localStorage survive restarts
        user_data_dir = str(_ROOT / get("browser.user_data_dir", "session/profile"))
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)

        context_kwargs = dict(
            user_agent=self._build_user_agent(),
            viewport={"width": resolution[0], "height": resolution[1]},
            locale=get("browser.locale", "en-US"),
            timezone_id=get("browser.timezone", "America/New_York"),
            permissions=["geolocation", "notifications"],
            java_script_enabled=True,
            accept_downloads=False,
            extra_http_headers=self._build_headers(),
            args=launch_args,
            headless=get("browser.headless", True),
            slow_mo=get("browser.slow_mo_ms", 0),
        )
        if proxy_settings:
            context_kwargs["proxy"] = proxy_settings

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir, **context_kwargs
        )

        # Apply all stealth patches before any page navigation
        await self._stealth.apply(self._context)

        # Set extra CDP overrides (screen size, etc.)
        await self._apply_cdp_overrides()

        log.info("engine.browser_ready")
        return self._context

    async def new_page(self):
        """Open a new page within the current context."""
        if not self._context:
            raise RuntimeError("BrowserEngine not started. Call start() first.")
        page = await self._context.new_page()
        await self._stealth.apply_to_page(page)
        return page

    async def stop(self):
        """Gracefully close browser."""
        try:
            if self._context:
                await self._context.close()
            if self._playwright:
                await self._playwright.stop()
            log.info("engine.stopped")
        except Exception as e:
            log.warning("engine.stop_error", error=str(e))

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _build_launch_args(self) -> list[str]:
        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-extensions-file-access-check",
            "--disable-default-apps",
            "--disable-translate",
            "--disable-sync",
            "--metrics-recording-only",
            "--hide-scrollbars",
            "--mute-audio",
            "--ignore-certificate-errors",
            "--ignore-ssl-errors",
            f"--window-size={self._stealth.fingerprint['resolution'][0]},{self._stealth.fingerprint['resolution'][1]}",
        ]
        return args

    def _build_proxy(self) -> Optional[dict]:
        if not get("proxy.enabled", False):
            return None
        env = self._cfg["_env"]
        host = env["proxy_host"]
        port = env["proxy_port"]
        user = env["proxy_user"]
        pwd  = env["proxy_pass"]
        if not host:
            log.warning("engine.proxy_enabled_but_not_configured")
            return None
        return {
            "server":   f"http://{host}:{port}",
            "username": user,
            "password": pwd,
        }

    def _build_user_agent(self) -> str:
        """Pick a realistic, current Chrome UA string."""
        ua_pool = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        ]
        return random.choice(ua_pool)

    def _build_headers(self) -> dict:
        """
        Extra headers applied to EVERY request - so only put things here that
        are correct for every request type.

        Sec-Fetch-*, Accept, Accept-Encoding and Upgrade-Insecure-Requests
        used to be forced here. They are per-request metadata: Chromium sends
        `Sec-Fetch-Dest: script` for a script, `image` for an image, and so
        on. Pinning them to the values for a top-level navigation made every
        subresource request self-describe as a document navigation, which is
        both trivially detectable and actively broken.

        It broke PerimeterX specifically: its captcha.js and challenge iframes
        never loaded, so the block page rendered "Error. Failed to display
        challenge." with no widget to press. Bisected 2026-09-01 - dropping
        these headers took the page from frames=1/no target to frames=7-8 with
        the press-and-hold control found, same IP, same cookies, minutes apart.

        Accept-Language is safe: it is genuinely constant across requests.
        """
        return {
            "Accept-Language": "en-US,en;q=0.9",
        }

    async def _apply_cdp_overrides(self):
        """Use Chrome DevTools Protocol to override additional properties."""
        pages = self._context.pages
        if not pages:
            pages = [await self._context.new_page()]
        for page in pages:
            try:
                cdp = await page.context.new_cdp_session(page)
                # Override screen metrics
                fp = self._stealth.fingerprint
                w, h = fp["resolution"]
                await cdp.send("Emulation.setDeviceMetricsOverride", {
                    "width":             w,
                    "height":            h,
                    "deviceScaleFactor": 1,
                    "mobile":            False,
                    "screenWidth":       w,
                    "screenHeight":      h,
                })
                await cdp.detach()
            except Exception as e:
                log.debug("engine.cdp_override_skipped", error=str(e))
