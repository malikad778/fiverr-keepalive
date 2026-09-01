#!/usr/bin/env python3
"""
scripts/solve_challenge.py
Launches the Chromium browser on EC2 with remote debugging enabled on port 9222.
Keeps running continuously so the user can connect via SSH tunnel and solve the challenge manually.
"""
import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config import load_config
from src.session.manager import save_cookies, load_cookies
from playwright.async_api import async_playwright

async def main():
    print("=" * 60)
    print("  Fiverr Keepalive - Interactive Challenge Solver")
    print("=" * 60)
    print("Starting Chromium on EC2 with Remote Debugging on port 9222...")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="/opt/fiverr-keepalive/session/profile",
            headless=False,
            args=[
                "--remote-debugging-port=9222",
                "--remote-debugging-address=0.0.0.0",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1366,768",
            ],
            viewport={"width": 1366, "height": 768},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        print("Loading current session cookies...")
        try:
            await load_cookies(context)
        except Exception as e:
            print(f"Cookie load note: {e}")

        print("Navigating to https://www.fiverr.com/ ...")
        try:
            await page.goto("https://www.fiverr.com/", wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"Navigation note: {e}")

        print("\n" + "=" * 60)
        print(">> Browser is now waiting for you to solve the challenge! <<")
        print("1. From your local PC, run the SSH tunnel:")
        print("   ssh -i C:\\Users\\Adnan\\.ssh\\fiverr-keepalive-new.pem -L 9222:localhost:9222 ubuntu@13.206.163.206")
        print("2. In your local Chrome, open: chrome://inspect/#devices")
        print("3. Click 'inspect' on the remote Fiverr tab.")
        print("4. Press & hold or solve the 'Human Touch' challenge.")
        print("=" * 60)

        # Resilient monitoring loop
        while True:
            try:
                await asyncio.sleep(4)
                if page.is_closed():
                    pages = context.pages
                    if pages:
                        page = pages[0]
                    else:
                        break

                title = await page.title()
                url = page.url
                print(f"[{datetime.now().strftime('%H:%M:%S')}] URL: {url} | Title: {title}")

                if title and "human touch" not in title.lower() and "captcha" not in title.lower() and "just a moment" not in title.lower():
                    print("\n[SUCCESS] Page title changed! PerimeterX challenge passed!")
                    await save_cookies(context)
                    print("Updated cookies saved to session database.")
                    # Keep open for another 30 seconds to settle cookies
                    await asyncio.sleep(30)
                    await save_cookies(context)
                    break
            except Exception as e:
                # Silently continue on temporary navigation or frame detach errors
                await asyncio.sleep(2)

        print("\nSaving final cookies before exit...")
        await save_cookies(context)
        print("Done. Closing browser...")
        await context.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting solver.")
