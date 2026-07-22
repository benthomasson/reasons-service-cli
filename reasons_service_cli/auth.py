"""OAuth login for reasons-service CLI.

Two login flows, tried in order:

1. **MCP OAuth discovery** (default) — no local credentials needed.
   The CLI discovers the server's OAuth endpoints via
   /.well-known/oauth-authorization-server, dynamically registers as a
   client, and goes through the server's authorize flow (which delegates
   to Google). The server issues MCP access/refresh tokens.

2. **Direct Google OAuth** (fallback) — used when google_client_id is
   configured locally. The CLI talks to Google directly and gets an
   ID token that reasons-service verifies.

Tokens are cached at ~/.config/reasons-service/token.json.
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


def _get_bearer_token(token: dict) -> str | None:
    """Extract the bearer token from a saved token dict (MCP or Google)."""
    return token.get("access_token") or token.get("id_token")


def get_token() -> str | None:
    """Get a valid bearer token, refreshing if needed. Returns None if no token."""
    token = _load_token()
    if not token:
        return None

    if _is_valid(token):
        return _get_bearer_token(token)

    new_token = _refresh_token(token)
    if new_token:
        _save_token(new_token)
        return _get_bearer_token(new_token)

    return None


# Keep old name as alias for client.py compatibility
get_id_token = get_token


# --- PKCE ---


def _generate_pkce() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge)."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return verifier, challenge


# --- MCP OAuth discovery flow ---


def _discover_oauth(server_url: str) -> dict | None:
    """Fetch OAuth metadata from the server. Returns None if not available."""
    url = server_url.rstrip("/")
    try:
        resp = httpx.get(f"{url}/.well-known/oauth-authorization-server", timeout=10.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _register_client(registration_endpoint: str, redirect_uri: str) -> dict:
    """Dynamically register as an OAuth client."""
    resp = httpx.post(
        registration_endpoint,
        json={
            "client_name": "reasons-service-cli",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        timeout=10.0,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Client registration failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def _mcp_exchange_code(token_endpoint: str, code: str, verifier: str,
                       client_id: str, redirect_uri: str) -> dict:
    """Exchange an MCP authorization code for tokens."""
    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "client_id": client_id,
        },
        timeout=30.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")

    token = resp.json()
    if "expires_in" in token:
        token["expires_at"] = time.time() + token["expires_in"]
    token["client_id"] = client_id
    token["token_endpoint"] = token_endpoint
    token["flow"] = "mcp"
    return token


def _mcp_login(server_url: str, port: int) -> dict:
    """Run the MCP OAuth discovery login flow. Returns token dict."""
    metadata = _discover_oauth(server_url)
    if not metadata:
        raise RuntimeError("Server does not support OAuth discovery")

    auth_endpoint = metadata["authorization_endpoint"]
    token_endpoint = metadata["token_endpoint"]
    reg_endpoint = metadata.get("registration_endpoint")

    if not reg_endpoint:
        raise RuntimeError("Server does not support dynamic client registration")

    redirect_uri = f"http://localhost:{port}/callback"

    # Dynamic client registration
    print("Registering with server...")
    client_info = _register_client(reg_endpoint, redirect_uri)
    client_id = client_info["client_id"]

    # PKCE
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{auth_endpoint}?{urlencode(params)}"

    print("Opening browser for login...")
    webbrowser.open(auth_url, new=2)

    code = _run_callback_server(port, state)
    print("Authorization code received.")

    token = _mcp_exchange_code(token_endpoint, code, verifier, client_id, redirect_uri)
    return token


# --- Direct Google OAuth flow (fallback) ---


def _google_exchange_code(code: str, verifier: str, client_id: str,
                          client_secret: str, redirect_uri: str) -> dict:
    """Exchange authorization code for Google tokens."""
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
    token["flow"] = "google"
    if client_secret:
        token["client_secret"] = client_secret
    return token


def _google_login(client_id: str, client_secret: str, port: int) -> dict:
    """Run the direct Google OAuth login flow. Returns token dict."""
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

    return _google_exchange_code(code, verifier, client_id, client_secret, redirect_uri)


# --- Token refresh ---


def _refresh_token(token: dict) -> dict | None:
    """Refresh an expired token. Handles both MCP and Google tokens."""
    refresh = token.get("refresh_token")
    client_id = token.get("client_id")
    if not refresh or not client_id:
        return None

    flow = token.get("flow", "google")

    if flow == "mcp":
        token_endpoint = token.get("token_endpoint")
        if not token_endpoint:
            return None
        return _mcp_refresh(token_endpoint, client_id, refresh)
    else:
        client_secret = token.get("client_secret", "")
        return _google_refresh(client_id, client_secret, refresh)


def _mcp_refresh(token_endpoint: str, client_id: str, refresh: str) -> dict | None:
    """Refresh an MCP token."""
    try:
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh,
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            logger.warning("MCP refresh failed (%d), need re-login", resp.status_code)
            return None
        new_token = resp.json()
        new_token["client_id"] = client_id
        new_token["token_endpoint"] = token_endpoint
        new_token["flow"] = "mcp"
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh
        if "expires_at" not in new_token and "expires_in" in new_token:
            new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token
    except Exception as e:
        logger.warning("MCP refresh error: %s", e)
        return None


def _google_refresh(client_id: str, client_secret: str, refresh: str) -> dict | None:
    """Refresh a Google token."""
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh,
    }
    if client_secret:
        data["client_secret"] = client_secret

    try:
        resp = httpx.post(GOOGLE_TOKEN_URL, data=data, timeout=30.0)
        if resp.status_code != 200:
            logger.warning("Google refresh failed (%d), need re-login", resp.status_code)
            return None
        new_token = resp.json()
        new_token["client_id"] = client_id
        new_token["flow"] = "google"
        if client_secret:
            new_token["client_secret"] = client_secret
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh
        if "expires_at" not in new_token and "expires_in" in new_token:
            new_token["expires_at"] = time.time() + new_token["expires_in"]
        return new_token
    except Exception as e:
        logger.warning("Google refresh error: %s", e)
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
        flow = token.get("flow", "google")
        print(f"Authenticated via {flow} ({remaining:.0f} minutes remaining)")
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

    bearer = _get_bearer_token(token)
    if not bearer:
        return False

    try:
        resp = httpx.get(
            f"{url}/api/domains",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=5.0,
        )
        return resp.status_code == 200
    except httpx.ConnectError:
        return True
    except Exception:
        return False


def login(port: int = 8085, force: bool = False) -> None:
    """Login to reasons-service.

    Tries MCP OAuth discovery first (no local credentials needed).
    Falls back to direct Google OAuth if google_client_id is configured.
    """
    config = load_config()
    server_url = config.get("url", "").rstrip("/")

    # Check existing token first (skip if --force)
    token = _load_token()
    if token and not force:
        if _is_valid(token):
            if _verify_against_server(token):
                remaining = (token.get("expires_at", 0) - time.time()) / 60
                print(f"Already authenticated ({remaining:.0f} minutes remaining)")
                return
            else:
                print(f"Token not accepted by {server_url}, starting login...")
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
                    print(f"Refreshed token not accepted by {server_url}, starting login...")
            else:
                print("Refresh failed, starting login...")
    elif force:
        print("Forcing re-login...")

    # Try MCP OAuth discovery first
    if server_url:
        metadata = _discover_oauth(server_url)
        if metadata:
            token = _mcp_login(server_url, port)
            _save_token(token)
            print("Login complete.")
            return

    # Fall back to direct Google OAuth
    client_id = config.get("google_client_id", "")
    if not client_id:
        if not server_url:
            print("Error: no server URL configured. Set REASONS_URL or run `reasons-service-cli init`.")
        else:
            print(f"Error: server at {server_url} does not support OAuth discovery,")
            print("and no google_client_id is configured for direct login.")
        return

    client_secret = config.get("google_client_secret", "")
    token = _google_login(client_id, client_secret, port)
    _save_token(token)
    print("Login complete.")
