#!/usr/bin/env python3
"""
scripts/first_run.py
Interactive first-run script — launches headed browser for one-time
manual login, then saves encrypted session cookies.

Run this ONCE on EC2 via SSH with X11 forwarding or noVNC:
  python scripts/first_run.py

After this, the daemon runs fully headless forever.
"""
import asyncio
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.prompt import Confirm

from src.utils.config import load_config, get
from src.utils.logger import setup_logging
from src.browser.engine import BrowserEngine
from src.browser.stealth import StealthPatcher
from src.session.auth import login, is_logged_in
from src.session.manager import save_cookies
from src.utils.crypto import encrypt

console = Console()


async def first_run():
    setup_logging("DEBUG")
    cfg = load_config()

    console.rule("[bold cyan]Fiverr Keepalive — First Run Setup[/bold cyan]")
    console.print()
    console.print("[yellow]This will open a headed browser for one-time login.[/yellow]")
    console.print("[dim]After login, session is saved and future runs are headless.[/dim]")
    console.print()

    # Validate .env is set up
    email    = cfg["_env"]["email"]
    username = cfg["_env"]["username"]
    password = cfg["_env"]["password"]
    secret   = cfg["_env"]["secret_key"]

    missing = []
    if not email:    missing.append("FIVERR_EMAIL")
    if not username: missing.append("FIVERR_USERNAME")
    if not password: missing.append("FIVERR_PASSWORD")
    if not secret:   missing.append("SECRET_KEY")

    if missing:
        console.print(f"[red]❌ Missing .env variables: {', '.join(missing)}[/red]")
        console.print("[dim]Copy .env.example to .env and fill in all values.[/dim]")
        sys.exit(1)

    console.print(f"[green]✓[/green] Credentials found for [bold]{email}[/bold]")

    # Force headed mode for first run
    import yaml
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(cfg_path) as f:
        raw_cfg = yaml.safe_load(f)

    original_headless = raw_cfg["browser"]["headless"]
    raw_cfg["browser"]["headless"] = False
    with open(cfg_path, "w") as f:
        yaml.dump(raw_cfg, f, default_flow_style=False)

    console.print("[cyan]▶ Launching headed browser...[/cyan]")

    engine = BrowserEngine()
    try:
        context = await engine.start()
        page    = context.pages[0] if context.pages else await engine.new_page()

        # Check if already logged in from existing profile
        already = await is_logged_in(page)
        if already:
            console.print("[green]✓ Already logged in from existing browser profile![/green]")
            await save_cookies(context)
            console.print("[green]✓ Session cookies saved and encrypted.[/green]")
        else:
            console.print("[yellow]Attempting automated login...[/yellow]")
            console.print("[dim]If a CAPTCHA appears, solve it manually in the browser.[/dim]")

            success = await login(page, context)
            if success:
                console.print("[green]✓ Login successful! Session saved.[/green]")
            else:
                console.print("[red]❌ Automated login failed.[/red]")
                console.print("[yellow]Please log in manually in the browser window.[/yellow]")
                console.print("[dim]The script will detect when you're logged in...[/dim]")

                # Wait for manual login
                for _ in range(60):  # wait up to 5 min
                    await asyncio.sleep(5)
                    if await is_logged_in(page):
                        await save_cookies(context)
                        console.print("[green]✓ Manual login detected! Session saved.[/green]")
                        break
                else:
                    console.print("[red]❌ Timed out waiting for manual login.[/red]")
                    sys.exit(1)

        console.print()
        console.print("[bold green]✅ First run complete![/bold green]")
        console.print("You can now start the daemon with:")
        console.print("  [cyan]python -m src.main[/cyan]  (development)")
        console.print("  [cyan]sudo systemctl start fiverr-keepalive[/cyan]  (production)")

    finally:
        await engine.stop()
        # Restore headless mode
        raw_cfg["browser"]["headless"] = True
        with open(cfg_path, "w") as f:
            yaml.dump(raw_cfg, f, default_flow_style=False)


if __name__ == "__main__":
    asyncio.run(first_run())
