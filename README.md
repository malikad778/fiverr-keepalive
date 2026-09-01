# Fiverr Keepalive Daemon - 24/7 Online Status Automation with Playwright

A production-grade, self-hosted Python daemon that keeps a Fiverr seller profile in a continuous **online / active state**, running unattended on any Linux server. Built on **Playwright** with human behaviour simulation, browser fingerprint management, encrypted session persistence, and a fully automated **PerimeterX "Press & Hold" challenge solver**.

Runs on **any VPS or home server** - AWS EC2, DigitalOcean, Hetzner, Linode, Vultr, Contabo, Oracle Cloud Free Tier, a Raspberry Pi, or bare metal. The only requirements are Linux, Python 3.11+, and systemd.

---

## Key Features

### Automated PerimeterX Challenge Solving
- Detects both block variants: the full-page block (`It needs a human touch`, `ERRCODE PXCR…`) and the injected modal (`Human verification`, `Before we continue…`).
- Locates the **Press & Hold** control across every frame - the widget is injected asynchronously and lives in an `about:blank` child frame, with the real control sealed inside a **closed shadow root**.
- Human-like press-and-hold: Bezier cursor approach, sub-pixel tremor while held, and holds until clearance is actually issued rather than for a fixed duration.
- Refusal circuit breaker: when PerimeterX declines to serve a challenge, backs off instead of hammering reloads.

### Anti-Detection Engine
- WebDriver flag overrides applied at CDP launch, not just in page JS.
- WebGL vendor/renderer, Canvas 2D noise, and AudioContext fingerprint management.
- Realistic plugin, language, permission, and hardware-concurrency profiles.
- Randomised viewport and user-agent selection per session.

### Human Behaviour Simulation
- Non-linear cursor paths using Bezier curves with acceleration and micro-jitter.
- Physics-based scrolling with reading pauses and occasional back-scrolling.
- Focus/blur cycles simulating browser multitasking.
- Weighted action mix: category browsing, gig views, inbox checks, notification polling.
- Proportional interval jitter so cycle timing never becomes machine-regular.

### Session and State Persistence
- Cookies encrypted at rest with **Fernet (AES-128-CBC + HMAC)** symmetric encryption.
- SQLite-backed cookie store surviving restarts and redeploys.
- Cookies re-persisted every cycle, so a restart always seeds from a current snapshot.
- **Lossless challenge recovery** - reload and re-solve rather than wiping cookies, since a wipe on an MFA-protected account cannot be undone unattended.
- Stale PerimeterX identity cookies stripped on load to avoid inheriting a flagged visitor.

### Operational Resilience
- systemd service with automatic restart and exponential backoff.
- Controlled browser recycling on uptime or cgroup memory pressure, so Chromium is never OOM-killed mid-cycle.
- Structured JSON logging via `structlog`, readable through `journalctl`.
- Optional AWS CloudWatch metric emission.

---

## Requirements

| | |
|---|---|
| OS | Any Linux distro with **systemd** (Ubuntu 22.04 LTS recommended) |
| Python | 3.11 or later |
| Display | **Xvfb** - required, see note below |
| Local machine | Google Chrome, for the one-time session capture |
| Resources | ~1 vCPU, 1.5 GB RAM (Chromium is the bulk of it) |

> **Headed mode is mandatory.** PerimeterX ignores the press-and-hold interaction entirely in headless Chromium - the button renders but the handler never reacts. The daemon therefore runs *headed* inside a virtual framebuffer (Xvfb), which `setup.sh` installs and configures automatically. Do not set `browser.headless: true`.

---

## Installation

### 1. Configure environment

Create a `.env` file in the project root:

```ini
FIVERR_USERNAME=your_fiverr_username
FIVERR_EMAIL=you@example.com
FIVERR_PASSWORD=your_password
SECRET_KEY=your_fernet_key
```

Generate a Fernet key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 2. Capture the session

Sessions are captured from a real browser on your own machine, which keeps the login fingerprint clean and satisfies any multi-factor prompt.

1. Close all Chrome windows.
2. Launch Chrome with remote debugging:
   ```cmd
   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\fiverr-keepalive\chrome_debug_profile"
   ```
3. Log in to Fiverr in that window.
4. Capture the cookies:
   ```bash
   python scripts/local_auth.py
   ```

This writes an encrypted cookie database to `session/store.db`.

> **Note on MFA:** if the account has multi-factor authentication enabled, automated password login cannot complete - Fiverr routes to `/mfa/generate` and waits for an emailed code. Cookie capture is then the only way to establish a session, and the daemon detects and reports this case explicitly rather than failing silently.

---

## Deployment (any Linux server)

Replace `<SERVER_IP>` with your host's address, `<SSH_KEY>` with your key path, and `<USER>` with your SSH user (`ubuntu`, `root`, `debian`, …).

### 1. Upload

```bash
tar --exclude=".git" --exclude="__pycache__" --exclude="venv" --exclude=".venv" \
    --exclude="session" --exclude="logs" --exclude="diagnostics" \
    -czf fiverr-keepalive.tar.gz src scripts setup.sh requirements.txt config.yaml .gitignore systemd

scp -i <SSH_KEY> fiverr-keepalive.tar.gz <USER>@<SERVER_IP>:~/
scp -i <SSH_KEY> .env                    <USER>@<SERVER_IP>:~/
scp -i <SSH_KEY> session/store.db        <USER>@<SERVER_IP>:~/
```

### 2. Install

```bash
ssh -i <SSH_KEY> <USER>@<SERVER_IP>

sudo mkdir -p /opt/fiverr-keepalive/session
sudo tar -xzf ~/fiverr-keepalive.tar.gz -C /opt/fiverr-keepalive
sudo mv ~/.env     /opt/fiverr-keepalive/.env
sudo mv ~/store.db /opt/fiverr-keepalive/session/store.db

cd /opt/fiverr-keepalive
chmod +x setup.sh
sudo ./setup.sh          # installs Python deps, Chromium, Xvfb and both systemd units
```

### 3. Run

```bash
sudo chown -R keepalive:keepalive /opt/fiverr-keepalive
sudo chmod 600 /opt/fiverr-keepalive/.env

sudo systemctl daemon-reload
sudo systemctl enable --now xvfb
sudo systemctl enable --now fiverr-keepalive
```

### 4. Monitor

```bash
sudo journalctl -u fiverr-keepalive -f
```

Healthy output looks like:

```
auth.session_valid_from_cookies
simulator.cycle_start          cycle=12
challenge.target_located       selector=div[aria-label*='Press & Hold' i]
challenge.solved               attempt=1
session.manager.cookies_saved  count=143
simulator.sleeping             seconds=287
```

---

## Configuration

All tunables live in `config.yaml`:

| Key | Purpose |
|---|---|
| `target.ping_interval_seconds` | Base cycle interval (jittered 0.7–1.35×) |
| `browser.headless` | **Must stay `false`** - see requirements |
| `browser.recycle_hours` / `recycle_memory_mb` | Controlled Chromium relaunch thresholds |
| `session.allow_cookie_wipe` | Destructive recovery; keep `false` with MFA enabled |
| `session.strip_px_cookies` | Drop stale PerimeterX visitor identity on load |
| `proxy.*` | Optional residential/datacenter proxy support |

---

## Diagnostics

```bash
# Dump the live challenge page: frames, matching selectors, bounding boxes, markup
python scripts/capture_challenge.py --headed
python scripts/capture_challenge.py --headed --solve   # also attempt a solve

# Cold login test (store.db is protected unless --save is passed)
python scripts/test_login.py --steps
```

---

## Project Structure

```
fiverr-keepalive/
├── config.yaml                    # Thresholds and behavioural configuration
├── requirements.txt
├── setup.sh                       # Server bootstrap: deps, Chromium, Xvfb, systemd
├── systemd/                       # Service unit definitions
├── src/
│   ├── main.py                    # Orchestrator, health loop, browser recycling
│   ├── browser/                   # Launch, stealth patching, fingerprints
│   ├── behavior/
│   │   ├── challenge.py           # PerimeterX detection + press-and-hold solver
│   │   ├── simulator.py           # Cycle loop
│   │   ├── mouse.py / scroll.py / idle.py
│   │   └── actions.py             # Fiverr-specific navigation
│   ├── session/
│   │   ├── manager.py             # Encrypted cookie store
│   │   ├── auth.py                # Login + session validation
│   │   └── google_auth.py         # "Continue with Google" SSO flow
│   ├── monitor/                   # Health checks and recovery
│   └── utils/                     # Config, logging, crypto
└── scripts/
    ├── local_auth.py              # One-time session capture
    ├── capture_challenge.py       # Challenge page diagnostics
    ├── test_login.py              # Cold login test
    └── test_session.py            # Session validation
```

---

## Troubleshooting

**`challenge.target_not_found`** - the widget had not rendered yet, or PerimeterX declined to serve one. Run `scripts/capture_challenge.py` to dump the live DOM.

**`Error. Failed to display challenge.`** - PerimeterX refused this visitor. The daemon backs off automatically. Reduce `ping_interval_seconds`, or route through a different egress IP.

**Challenge never reacts to the press** - confirm `browser.headless: false` and that `xvfb.service` is running (`systemctl status xvfb`).

**`auth.mfa_required`** - expected on MFA-protected accounts. Re-capture cookies with `scripts/local_auth.py`.

---

## About the Author

**Adnan Haider** - Automation Engineer and Backend Developer specialising in **browser automation, web scraping, anti-bot evasion, and DevOps**. GitHub: [@malikad778](https://github.com/malikad778)

I build reliable, long-running automation systems: Playwright and Selenium browser automation, headless Chromium infrastructure, CAPTCHA and bot-detection handling (PerimeterX, Cloudflare, reCAPTCHA), session and cookie management, proxy rotation, data extraction pipelines, REST API integrations, and Linux server deployment with Docker and systemd.

**Core stack:** Python · Playwright · Selenium · asyncio · SQLite · PostgreSQL · Linux · systemd · Docker · AWS · Bash

**Available for freelance work** in browser automation, web scraping, bot development, workflow automation, and cloud deployment. Open to collaboration - reach out via GitHub.

---

## Keywords

Fiverr automation · Fiverr online status bot · keep Fiverr profile active 24/7 · Playwright Python automation · headless browser automation · PerimeterX bypass · Press and Hold captcha solver · anti-bot detection · browser fingerprint spoofing · stealth browser automation · session persistence · encrypted cookie storage · human behaviour simulation · systemd daemon · VPS automation · self-hosted bot · Python web scraping · undetected automation

## Tags

`fiverr` `fiverr-bot` `fiverr-automation` `keep-alive` `online-status` `playwright` `playwright-python` `playwright-stealth` `browser-automation` `web-scraping` `perimeterx` `perimeterx-bypass` `captcha-solver` `press-and-hold` `anti-detection` `anti-bot` `stealth-browser` `undetected-chromedriver` `session-persistence` `cookie-management` `fingerprint-spoofing` `human-behavior-simulation` `headless-chrome` `xvfb` `systemd-service` `python` `asyncio` `automation` `devops` `vps` `self-hosted`

---

## Disclaimer

Provided for educational and personal-use purposes. Automating interactions with any platform may conflict with its Terms of Service - review Fiverr's terms and applicable law before deploying. You are responsible for how you use this software.
