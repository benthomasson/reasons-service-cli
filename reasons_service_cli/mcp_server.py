"""MCP server for reasons-service — exposes knowledge base tools directly.

Lets the user's LLM call reasons-service tools without going through
reasons-service's own LLM. Zero token cost, millisecond latency.
"""

import json

from mcp.server.fastmcp import FastMCP

from . import client

mcp = FastMCP("reasons-service")


def _resolve(domain: str) -> str:
    """Resolve domain name to UUID."""
    return client.resolve_domain(domain)


@mcp.tool()
def search(query: str, domain: str = "") -> str:
    """Search across beliefs, entries, and source documents.

    Returns matching beliefs (with IN/OUT truth values), entry titles,
    and source chunk snippets. Uses full-text search with stop-word
    filtering and term extraction.
    """
    domain_id = _resolve(domain or _default_domain())
    result = client.search(domain_id, query)
    return json.dumps(result, indent=2)


@mcp.tool()
def explain_belief(node_id: str, domain: str = "") -> str:
    """Explain why a belief is IN or OUT.

    Traces the justification chain: what supports this belief,
    what assumptions it rests on, and what would change if it
    were retracted.
    """
    domain_id = _resolve(domain or _default_domain())
    belief = client.get_belief(domain_id, node_id)
    explanation = client.explain(domain_id, node_id)
    return json.dumps({"belief": belief, "explanation": explanation}, indent=2)


@mcp.tool()
def what_if(node_id: str, action: str = "retract", domain: str = "") -> str:
    """Simulate retracting or asserting a belief without modifying the database.

    Shows the cascade: which beliefs would go OUT (retract) or come back IN
    (assert). Use this to understand the impact of changing a belief.

    Args:
        node_id: The belief ID to simulate
        action: "retract" or "assert"
        domain: Domain name (uses default if empty)
    """
    domain_id = _resolve(domain or _default_domain())
    result = client.what_if(domain_id, node_id, action)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_beliefs(status: str = "", domain: str = "") -> str:
    """List beliefs in the knowledge base.

    Args:
        status: Filter by truth value — "IN", "OUT", or empty for all
        domain: Domain name (uses default if empty)
    """
    domain_id = _resolve(domain or _default_domain())
    result = client.list_beliefs(domain_id, status=status or None)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_belief(node_id: str, domain: str = "") -> str:
    """Get full details for a specific belief including justifications and dependents."""
    domain_id = _resolve(domain or _default_domain())
    result = client.get_belief(domain_id, node_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_entries(topic: str = "", domain: str = "") -> str:
    """List analysis entries (reports, findings, assessments).

    Args:
        topic: Filter by topic slug, or empty for all entries
        domain: Domain name (uses default if empty)
    """
    domain_id = _resolve(domain or _default_domain())
    result = client.list_entries(domain_id, topic=topic or None)
    return json.dumps(result, indent=2)


@mcp.tool()
def get_entry(entry_id: str, domain: str = "") -> str:
    """Read the full content of an analysis entry."""
    domain_id = _resolve(domain or _default_domain())
    result = client.get_entry(domain_id, entry_id)
    return json.dumps(result, indent=2)


@mcp.tool()
def deep_search(query: str, domain: str = "") -> str:
    """Comprehensive search with IDF-ranked results from both beliefs and source documents.

    This is the recommended search tool — it runs the same dual-path retrieval
    as the server's /ask endpoint but returns structured context instead of
    a synthesized answer. Returns pre-ranked beliefs and source passages
    ready for you to synthesize an answer from.

    Use this instead of calling search + list_beliefs + get_entry separately.
    One call gives you everything you need.

    Args:
        query: The question or search terms
        domain: Domain name (uses default if empty)
    """
    domain_id = _resolve(domain or _default_domain())
    result = client.deep_search(domain_id, query)
    return json.dumps(result, indent=2)


@mcp.tool()
def list_domains() -> str:
    """List all available domains with belief/entry/source counts."""
    result = client.list_domains()
    return json.dumps(result, indent=2)


def _default_domain() -> str:
    """Get the default domain from config."""
    config = client._get_config()
    domain = config.get("project", "")
    if not domain:
        raise ValueError("No default domain configured. Pass domain= or set REASONS_PROJECT.")
    return domain


def main():
    """Entry point for the MCP server."""
    mcp.run()
