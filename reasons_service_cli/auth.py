"""Google OAuth login for reasons-service CLI.

Browser-based OAuth flow with localhost callback. Caches the ID token
and refresh token to ~/.config/reasons-service/token.json. The ID token is sent
as a Bearer header to reasons-service, which verifies it with Google
and looks up the user for RBAC.

Requires GOOGLE_CLIENT_ID (and optionally GOOGLE_CLIENT_SECRET) to be
set, or configured in ~/.config/reasons-service/config.toml.
"""

import hashlib
import base64
import json
import logging
import secrets
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Event
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

from .config import CONFIG_DIR, load_config

logger = logging.getLogger(__name__)

TOKEN_FILE = CONFIG_DIR / "token.json"
REFRESH_BUFFER_SECS = 60

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "openid email profile"


# --- Token storage ---


def _load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_token(token: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    TOKEN_FILE.chmod(0o600)
    logger.info("Token saved to %s", TOKEN_FILE)


def _is_valid(token: dict) -> bool:
    expires_at = token.get("expires_at", 0)
    return time.time() < expires_at - REFRESH_BUFFER_SECS


def get_id_token() -> str | None:
    """Get a valid ID token, refreshing if needed. Returns None if no token."""
    token = _load_token()
    if not token:
        return None

    if _is_valid(token):
        return token.get("id_token")

    # Try refresh
    new_token = _refresh_token(token)
    if new_token:
        _save_token(new_token)
        return new_token.get("id_token")

    return None


# --- OAuth helpers ---


def _generate_pkce() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


def _exchange_code(code: str, verifier: str, client_id: str,
                   client_secret: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret

    resp = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=30.0)
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")

    token = resp.json()
    if "expires_in" in token:
        token["expires_at"] = time.time() + token["expires_in"]
    token["client_id"] = client_id
    if client_secret:
        token["client_secret"] = client_secret
    return token


def _refresh_token(token: dict) -> dict | None:
    """Refresh an expired token. Returns new token or None."""
    refresh = token.get("refresh_token")
    client_id = token.get("client_id")
    if not refresh or not client_id:
        return None

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh,
    }
    client_secret = token.get("client_secret", "")
    if client_secret:
        data["client_secret"] = client_secret

    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=30.0)
        if resp.status_code != 200:
            logger.warning("Refresh failed (%d), need re-login", resp.status_code)
            return None
        new_token = resp.json()
        new_token["client_id"] = client_id
        if client_secret:
            new_token["client_secret"] = client_secret
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh
        if "expires_at" not in new_token and "expires_in" in new_token:
            new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token
    except Exception as e:
        logger.warning("Refresh error: %s", e)
        return None


# --- Callback server ---


SUCCESS_HTML = """\
<!DOCTYPE html>
<html><head><title>Authenticated</title>
<style>
body { font-family: system-ui; background: #1a1a2e; color: #eee;
       display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
.card { text-align: center; padding: 2em 3em; background: #16213e; border-radius: 12px; }
.ok { font-size: 3em; color: #4ade80; }
</style></head>
<body><div class="card">
<div class="ok">&#x2713;</div>
<h2>Authenticated</h2>
<p>You can close this tab.</p>
</div>
<script>setTimeout(()=>window.close(),2000)</script>
</body></html>
"""

ERROR_HTML = """\
<!DOCTYPE html>
<html><head><title>Error</title>
<style>
body { font-family: system-ui; background: #1a1a2e; color: #eee;
       display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
.card { text-align: center; padding: 2em 3em; background: #16213e; border-radius: 12px; }
.err { font-size: 3em; color: #f87171; }
</style></head>
<body><div class="card">
<div class="err">&#x2717;</div>
<h2>Authentication Failed</h2>
<p>%s</p>
</div></body></html>
"""


def _run_callback_server(port: int, expected_state: str) -> str:
    """Start HTTP server, wait for OAuth callback, return auth code."""
    auth_code = None
    done = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            state = params.get("state", [None])[0]
            code = params.get("code", [None])[0]
            error = params.get("error", [None])[0]

            if state != expected_state:
                self._respond(400, ERROR_HTML % "State mismatch.")
                return
            if error or not code:
                self._respond(400, ERROR_HTML % (error or "No authorization code."))
                return

            auth_code = code
            self._respond(200, SUCCESS_HTML)
            done.set()

        def _respond(self, status, html):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", port), Handler)
    server.timeout = 120

    logger.info("Waiting for browser callback on localhost:%d ...", port)
    while not done.is_set():
        server.handle_request()
        if auth_code:
            break

    server.server_close()

    if not auth_code:
        raise RuntimeError("No authorization code received (timeout or error)")
    return auth_code


# --- Main flow ---


def check_token() -> bool:
    """Check if a valid token exists. Returns True if valid."""
    token = _load_token()
    if not token:
        print("No cached token found.")
        return False
    if _is_valid(token):
        remaining = (token.get("expires_at", 0) - time.time()) / 60
        print(f"Authenticated ({remaining:.0f} minutes remaining)")
        return True
    print("Token expired, attempting refresh...")
    new_token = _refresh_token(token)
    if new_token:
        _save_token(new_token)
        remaining = (new_token.get("expires_at", 0) - time.time()) / 60
        print(f"Token refreshed ({remaining:.0f} minutes remaining)")
        return True
    print("Refresh failed — run `reasons-service-cli login` to re-authenticate.")
    return False


def _verify_against_server(token: dict) -> bool:
    """Check if the token is accepted by the configured reasons-service URL."""
    config = load_config()
    url = config.get("url", "").rstrip("/")
    if not url:
        return True

    id_token = token.get("id_token")
    if not id_token:
        return False

    try:
        resp = httpx.get(
            f"{url}/api/domains",
            headers={"Authorization": f"Bearer {id_token}"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except httpx.ConnectError:
        return True
    except Exception:
        return False


def login(port: int = 8085, force: bool = False) -> None:
    """Run the full Google OAuth browser login flow."""
    config = load_config()
    client_id = config.get("google_client_id", "")
    client_secret = config.get("google_client_secret", "")

    if not client_id:
        print("Error: google_client_id not set in config or GOOGLE_CLIENT_ID env var.")
        print("Add to ~/.config/reasons-service/config.toml:")
        print('  google_client_id = "your-client-id.apps.googleusercontent.com"')
        return

    token = _load_token()
    if token and not force:
        if _is_valid(token):
            if _verify_against_server(token):
                remaining = (token.get("expires_at", 0) - time.time()) / 60
                print(f"Already authenticated ({remaining:.0f} minutes remaining)")
                return
            else:
                url = config.get("url", "")
                print(f"Token not accepted by {url}, starting browser login...")
        else:
            print("Token expired, trying refresh...")
            new_token = _refresh_token(token)
            if new_token:
                _save_token(new_token)
                if _verify_against_server(new_token):
                    remaining = (new_token.get("expires_at", 0) - time.time()) / 60
                    print(f"Token refreshed ({remaining:.0f} minutes remaining)")
                    return
                else:
                    url = config.get("url", "")
                    print(f"Refreshed token not accepted by {url}, starting browser login...")
            else:
                print("Refresh failed, starting browser login...")
    elif force:
        print("Forcing re-login...")

    redirect_uri = f"http://localhost:{port}/callback"

    verifier, challenge = _generate_pkce()

    state = secrets.token_urlsafe(32)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    print("Opening browser for Google login...")
    webbrowser.open(auth_url, new=2)

    code = _run_callback_server(port, state)
    print("Authorization code received.")

    token = _exchange_code(code, verifier, client_id, client_secret, redirect_uri)
    _save_token(token)
    print("Login complete.")
