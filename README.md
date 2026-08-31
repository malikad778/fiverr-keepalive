# Fiverr Keepalive Daemon

A production-grade, headless background system designed to maintain a continuous, trusted active state on Fiverr profiles. Built on top of Playwright, it uses advanced anti-detection techniques, custom browser fingerprinting, and human behavioral simulation to remain online 24/7 without triggering automated challenges.

---

## Technical Features

### Anti-Detection Engine
- WebDriver flag overrides (patched at V8/CDP launch, not just JS).
- WebGL vendor and renderer string spoofing matching real hardware configurations.
- Dynamic Canvas 2D fingerprinting with session-specific noise.
- Audio context frequency modification.
- Pre-populated plugin, language, and permission lists matching real user systems.

### Human Behavior Simulation
- Non-linear cursor movements using arbitrary-degree Bezier curves with custom acceleration and micro-jitter.
- Physics-based page scrolling featuring variable speed curves, pauses to read content, and random back-scrolling.
- Focus and blur event loops that simulate standard browser multitasking.
- Weighted action triggers mimicking real traffic patterns (discover browsing, viewing gig details, checking message inbox, and loading profile alerts).

### Session and State Persistence
- Cookies encrypted at rest using 256-bit Fernet symmetric encryption.
- SQLite cookie database storing session details for persistency across restarts.
- Auto-recovery loops with exponential backoff configurations.

---

## Installation and Configuration

### Prerequisites
- Python 3.11 or later
- Google Chrome (locally installed for session capture)
- AWS EC2 instance running Ubuntu 22.04 LTS

### 1. Local Configuration
Create a `.env` file in the root folder of the project.
```ini
FIVERR_USERNAME=your_fiverr_username
SECRET_KEY=your_fernet_key
```

### 2. Capturing the Session Cookies
Fiverr sessions must be captured via remote debugging to ensure anti-bot integrity:
1. Close all active Google Chrome windows on your PC.
2. Launch a debug browser via Command Prompt:
   ```cmd
   chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\fiverr-keepalive\chrome_debug_profile"
   ```
3. Open Fiverr in the debug window and log in.
4. Run the local helper script in PowerShell:
   ```powershell
   python scripts/local_auth.py
   ```
This generates your encrypted cookie database locally at `session/store.db`.

---

## Remote Server Deployment

### 1. File Upload
Upload the files to your EC2 instance. (Replace `<EC2_IP>` with the public IP and `<PEM_KEY>` with your SSH key path):
```powershell
# Copy archive to server
tar --exclude=".git" --exclude="__pycache__" --exclude="venv" --exclude=".venv" --exclude="session" --exclude="logs" -czf fiverr-keepalive.tar.gz src scripts setup.sh requirements.txt config.yaml .gitignore systemd
scp -i <PEM_KEY> fiverr-keepalive.tar.gz ubuntu@<EC2_IP>:/home/ubuntu/
scp -i <PEM_KEY> .env ubuntu@<EC2_IP>:/home/ubuntu/
scp -i <PEM_KEY> session/store.db ubuntu@<EC2_IP>:/home/ubuntu/
```

### 2. Extract and Bootstrap
SSH into your server and run the extraction and bootstrap script:
```bash
ssh -i <PEM_KEY> ubuntu@<EC2_IP>
sudo mkdir -p /opt/fiverr-keepalive/session
sudo tar -xzf /home/ubuntu/fiverr-keepalive.tar.gz -C /opt/fiverr-keepalive
sudo mv /home/ubuntu/.env /opt/fiverr-keepalive/.env
sudo mv /home/ubuntu/store.db /opt/fiverr-keepalive/session/store.db

cd /opt/fiverr-keepalive
chmod +x setup.sh
sudo ./setup.sh
```

### 3. Startup and Monitoring
Run the daemon as a systemd service:
```bash
sudo chown -R keepalive:keepalive /opt/fiverr-keepalive
sudo chmod 600 /opt/fiverr-keepalive/.env
sudo chmod 644 /opt/fiverr-keepalive/session/store.db

sudo systemctl daemon-reload
sudo systemctl enable fiverr-keepalive
sudo systemctl start fiverr-keepalive
```

Monitor logs using standard system tools:
```bash
sudo journalctl -u fiverr-keepalive -f
```

---

## Project Structure

```
fiverr-keepalive/
├── config.yaml          # Daemon thresholds and behavioral configurations
├── requirements.txt     # Python requirements
├── setup.sh             # Remote system setup and dependency installer
├── src/
│   ├── main.py          # Orchestrator and service runner
│   ├── browser/         # Browser initialization and anti-detection modules
│   ├── behavior/        # Human simulator, cursor pathing, and scrolling logic
│   ├── session/         # Session persistence and cookie SQLite storage
│   ├── monitor/         # Log parsing, challenge detection, and health checks
│   └── utils/           # Helper scripts, logging setup, and crypto
└── scripts/
    ├── local_auth.py    # Local cookie capturing script
    └── test_session.py  # Health validation script
```

---

## About the Author

This software is developed and maintained by **Adnan Haider** (GitHub: [@malikad778](https://github.com/malikad778)). It was designed to solve session-persistence challenges on remote systems while maintaining platform trust guidelines.

---

## Tags
`fiverr`, `keep-alive`, `playwright-stealth`, `browser-automation`, `session-persistence`, `aws-ec2`, `anti-detection`, `python`, `systemd-service`, `automation-stealth`
