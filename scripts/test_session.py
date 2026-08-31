#!/usr/bin/env python3
"""
scripts/test_session.py
Validates that the stored session is alive and the profile is reachable.
Run this anytime to verify the daemon is healthy.

  python scripts/test_session.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from src.utils.config import load_config, get
from src.utils.logger import setup_logging
from src.browser.engine import BrowserEngine
from src.session.manager import load_cookies
from src.session.auth import is_logged_in
from src.monitor.health import HealthMonitor

console = Console()


async def test():
    setup_logging("WARNING")
    cfg = load_config()

    console.rule("[bold cyan]Session Health Check[/bold cyan]")

    engine = BrowserEngine()
    try:
        context = await engine.start()
        page    = context.pages[0] if context.pages else await engine.new_page()

        results = {}

        # Test 1: Cookie load
        console.print("[dim]Testing cookie storage...[/dim]")
        cookies_ok = await load_cookies(context)
        results["Stored Cookies"] = ("✅ Found", "green") if cookies_ok else ("❌ None", "red")

        # Test 2: Login check
        console.print("[dim]Checking session validity...[/dim]")
        logged_in = await is_logged_in(page)
        results["Session Valid"] = ("✅ Logged In", "green") if logged_in else ("❌ Not logged in", "red")

        # Test 3: Health check
        console.print("[dim]Running page health check...[/dim]")
        monitor = HealthMonitor()
        health  = await monitor.check_page(page)
        h_text  = f"✅ {health['status']}" if health["ok"] else f"❌ {health['status']}: {health['issue']}"
        h_color = "green" if health["ok"] else "red"
        results["Page Health"] = (h_text, h_color)

        # Test 4: Proxy (if enabled)
        if get("proxy.enabled", False):
            from src.proxy.rotator import ProxyRotator
            console.print("[dim]Validating proxy...[/dim]")
            pr = ProxyRotator()
            pv = await pr.validate()
            p_text  = f"✅ {pv['ip']} ({pv['country']})" if pv["ok"] else "❌ Proxy failed"
            p_color = "green" if pv["ok"] else "red"
            results["Proxy"] = (p_text, p_color)

        # Print results table
        table = Table(title="Session Status", border_style="cyan")
        table.add_column("Check", style="bold")
        table.add_column("Result")
        for check, (text, color) in results.items():
            table.add_row(check, f"[{color}]{text}[/{color}]")
        console.print(table)

        all_ok = all(c == "green" for _, c in results.values())
        if all_ok:
            console.print("\n[bold green]✅ All checks passed — daemon is healthy![/bold green]")
            return 0
        else:
            console.print("\n[bold red]❌ Some checks failed. Run first_run.py to re-establish session.[/bold red]")
            return 1

    finally:
        await engine.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(test()))
