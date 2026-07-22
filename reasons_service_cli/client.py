"""HTTP client for reasons-service API."""

import httpx

from .config import load_config


TIMEOUT = 120.0

_config = None


def _get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _base_url() -> str:
    url = _get_config()["url"]
    if not url:
        raise ValueError("No URL configured. Set REASONS_URL or run `reasons-service-cli init`.")
    return url.rstrip("/")


def _headers() -> dict[str, str]:
    config = _get_config()
    headers = {}
    api_key = config["api_key"]
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        from .auth import get_token
        token = get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def health() -> dict:
    """Check server health and capabilities."""
    resp = httpx.get(f"{_base_url()}/health", timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def list_domains() -> list[dict]:
    """List all domains."""
    resp = httpx.get(
        f"{_base_url()}/api/domains",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def resolve_domain(name_or_id: str) -> str:
    """Resolve a domain name to its UUID. Returns the ID if already a UUID."""
    if len(name_or_id) == 36 and name_or_id.count("-") == 4:
        return name_or_id

    resp = httpx.get(
        f"{_base_url()}/api/domains/resolve",
        params={"name": name_or_id},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    if resp.status_code == 200:
        return resp.json()["id"]

    domains = list_domains()
    for d in domains:
        if d["name"] == name_or_id:
            return d["id"]

    matches = [d for d in domains if name_or_id.lower() in d["name"].lower()]
    if len(matches) == 1:
        return matches[0]["id"]
    if len(matches) > 1:
        names = [m["name"] for m in matches]
        raise ValueError(f"Ambiguous domain name '{name_or_id}': {names}")

    available = [d["name"] for d in domains]
    raise ValueError(f"Domain '{name_or_id}' not found. Available: {available}")


def ask(domain_id: str, question: str) -> dict:
    """Ask a question (non-streaming, returns complete answer)."""
    body = {"question": question}
    resp = httpx.post(
        f"{_base_url()}/api/domains/{domain_id}/ask",
        json=body,
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def search(domain_id: str, query: str) -> dict:
    """Search beliefs and entries."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/search",
        params={"q": query},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def explain(domain_id: str, node_id: str) -> dict:
    """Explain why a belief is IN or OUT."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/beliefs/{node_id}/explain",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_belief(domain_id: str, node_id: str) -> dict:
    """Get full details for a belief."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/beliefs/{node_id}",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def what_if(domain_id: str, node_id: str, action: str = "retract") -> dict:
    """Simulate retracting or asserting a belief."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/beliefs/{node_id}/what-if",
        params={"action": action},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def list_beliefs(domain_id: str, status: str | None = None) -> dict:
    """List beliefs, optionally filtered by status (IN/OUT)."""
    params = {}
    if status:
        params["status"] = status
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/beliefs",
        params=params,
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def list_entries(domain_id: str, topic: str | None = None) -> list[dict]:
    """List entries for a domain."""
    params = {}
    if topic:
        params["topic"] = topic
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/entries",
        params=params,
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_entry(domain_id: str, entry_id: str) -> dict:
    """Get full entry content."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/entries/{entry_id}",
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def deep_search(domain_id: str, query: str) -> dict:
    """Dual-path retrieval with IDF ranking — no LLM needed."""
    resp = httpx.get(
        f"{_base_url()}/api/domains/{domain_id}/deep-search",
        params={"q": query},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def create_domain(name: str, description: str = "") -> dict:
    """Create a new domain. Returns domain dict with id."""
    resp = httpx.post(
        f"{_base_url()}/api/domains",
        json={"name": name, "description": description},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def import_reasons(db_path: str, name: str, description: str = "") -> dict:
    """Upload a reasons.db to create a domain with beliefs."""
    with open(db_path, "rb") as f:
        resp = httpx.post(
            f"{_base_url()}/api/domains/import-reasons",
            files={"file": ("reasons.db", f, "application/octet-stream")},
            data={"name": name, "description": description},
            headers=_headers(),
            timeout=TIMEOUT,
        )
    resp.raise_for_status()
    return resp.json()
