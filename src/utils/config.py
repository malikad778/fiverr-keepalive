"""
src/utils/config.py - Centralised configuration loader.
Merges config.yaml values with .env overrides.
"""
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

# Load .env from project root
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

_config: dict = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(path: str | None = None) -> dict:
    """Load and cache configuration from config.yaml."""
    global _config
    if _config:
        return _config

    cfg_path = Path(path) if path else _ROOT / "config.yaml"
    with open(cfg_path, "r") as f:
        _config = yaml.safe_load(f)

    # Inject env overrides
    _config["target"]["base_url"] = os.getenv(
        "TARGET_URL", _config["target"]["base_url"]
    )
    username = os.getenv("FIVERR_USERNAME", "")
    _config["target"]["profile_url"] = (
        _config["target"]["base_url"]
        + _config["target"]["profile_path"].format(username=username)
    )
    _config["_env"] = {
        "username": username,
        "email": os.getenv("FIVERR_EMAIL", ""),
        "password": os.getenv("FIVERR_PASSWORD", ""),
        # Google SSO - used by challenge recovery ("Continue with Google").
        # google_email defaults to the Fiverr email when unset.
        "google_email": os.getenv("GOOGLE_EMAIL", "") or os.getenv("FIVERR_EMAIL", ""),
        "google_password": os.getenv("GOOGLE_PASSWORD", ""),
        "secret_key": os.getenv("SECRET_KEY", ""),
        "proxy_host": os.getenv("PROXY_HOST", ""),
        "proxy_port": int(os.getenv("PROXY_PORT", "22225")),
        "proxy_user": os.getenv("PROXY_USERNAME", ""),
        "proxy_pass": os.getenv("PROXY_PASSWORD", ""),
        "alert_email": os.getenv("ALERT_EMAIL", ""),
        "aws_region": os.getenv("AWS_REGION", "us-east-1"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }
    return _config


def get(key_path: str, default=None):
    """
    Dot-path accessor.
    e.g. get('browser.headless') -> True
    """
    cfg = load_config()
    keys = key_path.split(".")
    node = cfg
    for k in keys:
        if isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return default
    return node
