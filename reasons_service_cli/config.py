"""Configuration file support.

Reads config from three layers (highest priority wins):

    1. Environment variables (REASONS_URL, REASONS_PROJECT, etc.)
    2. Local .reasons-service.toml — searched upward from cwd to filesystem root
    3. Global ~/.config/reasons-service/config.toml

CLI flags override all three.

Local config (.reasons-service.toml) example — put in your repo root:

    project = "my-domain"
    url = "https://reasons.example.com"

Global config (~/.config/reasons-service/config.toml):

    [default]
    url = "https://reasons.example.com"
    api_key = "your-key"
    project = "my-domain"
    google_client_id = "your-id.apps.googleusercontent.com"
    google_client_secret = "your-secret"
"""

import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "reasons-service"
CONFIG_FILE = CONFIG_DIR / "config.toml"
LOCAL_CONFIG_NAME = ".reasons-service.toml"


def _parse_toml(path: Path) -> dict:
    """Parse a simple TOML file. Handles [sections] and key = "value" pairs.

    Keys before any section header are placed in the "default" section.
    """
    if not path.exists():
        return {}

    config = {}
    current_section = "default"
    config[current_section] = {}

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            config.setdefault(current_section, {})
        elif "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            config[current_section][key] = value

    return config


def _find_local_config() -> Path | None:
    """Search upward from cwd for .reasons-service.toml."""
    current = Path.cwd()
    while True:
        candidate = current / LOCAL_CONFIG_NAME
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_config() -> dict:
    """Load config from local file, global file, and environment.

    Priority: environment variables > local .reasons-service.toml > global config.toml.
    """
    global_config = _parse_toml(CONFIG_FILE).get("default", {})

    local_path = _find_local_config()
    local_config = _parse_toml(local_path).get("default", {}) if local_path else {}

    def _get(key: str, env_var: str, default: str = "") -> str:
        return os.environ.get(env_var) or local_config.get(key) or global_config.get(key, default)

    config = {
        "url": _get("url", "REASONS_URL"),
        "api_key": _get("api_key", "REASONS_API_KEY"),
        "project": _get("project", "REASONS_PROJECT"),
        "google_client_id": _get("google_client_id", "GOOGLE_CLIENT_ID"),
        "google_client_secret": _get("google_client_secret", "GOOGLE_CLIENT_SECRET"),
        "llm": _get("llm", "REASONS_LLM_MODEL"),
        "anonymous": _get("anonymous", "REASONS_ANONYMOUS", "").lower() in ("true", "1", "yes"),
    }
    if not config["anonymous"] and not config["api_key"] and not config["google_client_id"]:
        config["anonymous"] = True
    return config


def init_config():
    """Create a default config file if one doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        print(f"Config already exists: {CONFIG_FILE}")
        return

    CONFIG_FILE.write_text("""\
[default]
url = "http://localhost:8000"
# project = "my-domain"
# api_key = "your-api-key"
# anonymous = false

# For Google OAuth:
# google_client_id = "your-id.apps.googleusercontent.com"
# google_client_secret = "your-secret"
""")
    print(f"Created config: {CONFIG_FILE}")
