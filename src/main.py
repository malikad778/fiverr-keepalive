"""
src/main.py
Master orchestrator — the entry point for the keepalive daemon.

Flow:
  1. Load config + logging
  2. Validate proxy connectivity
  3. Launch stealth browser
  4. Load/restore session (cookies)
  5. Verify logged in (re-login if needed)
  6. Start behavior simulation loop
  7. Health monitor runs concurrently
  8. On failure: auto-recover via RecoveryManager
  9. On unrecoverable error: systemd will restart via Restart=always
"""
import asyncio
import signal
import sys
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .browser import BrowserEngine
from .session import ensure_logged_in, save_cookies
from .behavior import BehaviorSimulator
from .monitor import HealthMonitor, RecoveryManager
from .proxy import ProxyRotator
from .utils import load_config, setup_logging, get_logger, get

console = Console()


async def _banner():
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    text = Text()
    text.append("🚀 Fiverr Keepalive Daemon\n", style="bold cyan")
    text.append(f"   Started: {now}\n", style="dim")
    text.append(f"   Profile: {get('_env.username', 'N/A')}\n", style="dim")
    text.append(f"   Proxy:   {'enabled' if get('proxy.enabled') else 'disabled'}\n", style="dim")
    console.print(Panel(text, border_style="cyan", padding=(0, 2)))


async def run_daemon():
    """Main daemon loop — runs indefinitely until interrupted."""
    cfg = load_config()
    setup_logging(cfg["_env"]["log_level"])
    log = get_logger("main")

    await _banner()

    # ── 1. Validate Proxy ─────────────────────────────────
    proxy_rotator = None
    if get("proxy.enabled", False):
        proxy_rotator = ProxyRotator()
        proxy_rotator.start_session()
        log.info("main.validating_proxy")
        proxy_info = await proxy_rotator.validate()
        if not proxy_info["ok"]:
            log.error("main.proxy_validation_failed")
            if not get("proxy.fallback_to_direct", False):
                sys.exit(1)
        else:
            log.info("main.proxy_ok", ip=proxy_info["ip"], country=proxy_info["country"])

    # ── 2. Initialize subsystems ──────────────────────────
    engine   = BrowserEngine()
    health   = HealthMonitor()
    recovery = RecoveryManager(health)

    # Declared here so _shutdown can close over it. Previously this used
    # `'simulator' in dir()`, which inside a nested function inspects that
    # function's own locals — always empty, so the condition was never true
    # and stop() was never called (and would not have been awaited anyway).
    simulator = None

    async def _shutdown(signame: str):
        log.info("main.shutdown_signal", signal=signame)
        if simulator is not None:
            await simulator.stop()
        await engine.stop()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: asyncio.create_task(_shutdown(s)))
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    # ── 3. Start Browser ──────────────────────────────────
    restart_attempts = 0
    max_restarts = 10

    while restart_attempts < max_restarts:
        try:
            log.info("main.starting_browser", attempt=restart_attempts + 1)
            context = await engine.start()
            page    = context.pages[0] if context.pages else await engine.new_page()

            # ── 4. Session Management ─────────────────────
            log.info("main.ensuring_session")
            session_ok = await ensure_logged_in(page, context)
            if not session_ok:
                # Usually this means PerimeterX is blocking the page, not that
                # anything is misconfigured. Relaunching Chromium every 60s
                # just feeds more blocked requests to an already-escalated
                # visitor, so back off hard and give the block time to decay.
                restart_attempts += 1
                backoff = min(900, 60 * (2 ** (restart_attempts - 1)))
                log.error("main.cannot_establish_session",
                          attempt=restart_attempts, backoff_seconds=backoff)
                await engine.stop()
                await asyncio.sleep(backoff)
                continue

            log.info("main.session_ready")

            # ── 5. Start Behavior Simulator ───────────────
            simulator = BehaviorSimulator(page)
            await simulator.start()

            # ── 6. Health monitor drives the lifetime; the simulator runs
            #      as its own cancellable task rather than a gather() arm,
            #      so it can be stopped and restarted cleanly.
            try:
                await _health_loop(health, recovery, page, context, simulator, engine)
            finally:
                await simulator.stop()

        except asyncio.CancelledError:
            log.info("main.cancelled")
            break
        except Exception as e:
            log.error("main.unhandled_error", error=str(e), restart=restart_attempts)
            restart_attempts += 1
            backoff = min(30 * restart_attempts, 300)
            await asyncio.sleep(backoff)
        finally:
            try:
                await engine.stop()
            except Exception:
                pass

    log.error("main.max_restarts_reached", max=max_restarts)


async def _health_loop(
    health: HealthMonitor,
    recovery: RecoveryManager,
    page,
    context,
    simulator: BehaviorSimulator,
    engine: BrowserEngine,
) -> None:
    """Concurrent health monitoring task."""
    log = get_logger("main.health_loop")
    interval = get("monitor.check_interval_seconds", 600)

    while True:
        await asyncio.sleep(interval)
        result = await health.check_page(page)
        log.info("health_loop.check", status=result["status"])

        if not result["ok"]:
            log.warning("health_loop.unhealthy", result=result)
            # stop() now waits for the cycle to actually exit, so recovery
            # never runs concurrently with a live cycle on the same page.
            await simulator.stop()
            recovered = await recovery.handle_unhealthy(page, context, result)
            if recovered:
                # Save fresh cookies after recovery
                await save_cookies(context)
                # Restart via the owned task. Re-calling __init__() and
                # spawning a bare create_task() left the previous loop alive.
                await simulator.start()
            else:
                log.error("health_loop.recovery_failed_restarting_browser")
                await engine.stop()
                raise RuntimeError("Unrecoverable session failure — restarting browser")

        # Rotate proxy if sticky session expired
        from .proxy import ProxyRotator
        # Proxy rotation would require browser restart — log for now
        # Future: implement context-level proxy swap


def main():
    """CLI entry point."""
    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Shutting down...[/yellow]")


if __name__ == "__main__":
    main()
