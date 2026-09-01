import asyncio
import sys
from pathlib import Path
from playwright.async_api import async_playwright

async def main():
    print("Launching browser...")
    async with async_playwright() as p:
        # Load user data dir to use the logged-in session
        context = await p.chromium.launch_persistent_context(
            "/opt/fiverr-keepalive/session/profile",
            headless=True,
            viewport={"width": 1280, "height": 800}
        )
        page = context.pages[0] if context.pages else await context.new_page()
        
        print("Navigating to Fiverr...")
        await page.goto("https://www.fiverr.com", wait_until="domcontentloaded", timeout=45000)
        await asyncio.sleep(5)
        
        # Take screenshot
        screenshot_path = "/opt/fiverr-keepalive/screenshot.png"
        await page.screenshot(path=screenshot_path)
        print(f"Screenshot successfully saved to: {screenshot_path}")
        
        # Print page title and url
        title = await page.title()
        url = page.url
        print(f"Page Title: {title}")
        print(f"Page URL: {url}")
        
        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
