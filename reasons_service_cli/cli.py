"""CLI for reasons-service.

Usage:
    reasons-service-cli ask <question> [--domain NAME] [--model MODEL]
    reasons-service-cli ask-local <question> [--domain NAME] [--model MODEL]
    reasons-service-cli deep-search <query> [--domain NAME]
    reasons-service-cli search <query> [--domain NAME]
    reasons-service-cli beliefs [--domain NAME] [--status IN|OUT]
    reasons-service-cli domains
    reasons-service-cli explain <belief-id> [--domain NAME]
    reasons-service-cli import-reasons <path> --name NAME [--description DESC]
    reasons-service-cli login [--force]               Google OAuth login (browser flow)
    reasons-service-cli logout                        Clear cached credentials
    reasons-service-cli status                        Check authentication status
    reasons-service-cli init                          Create config at ~/.config/reasons-service/config.toml

Config priority (highest wins):
    CLI flags > env vars > .reasons-service.toml (local) > ~/.config/reasons-service/config.toml (global)

Local config (.reasons-service.toml in repo root):
    project = "my-domain"

Global config (~/.config/reasons-service/config.toml):
    [default]
    url = "https://reasons.example.com"
    project = "my-domain"
    google_client_id = "your-id.apps.googleusercontent.com"
    google_client_secret = "your-secret"
"""

import sys

from . import client
from .config import load_config


HELP = {
    "ask": "Usage: reasons-service-cli ask <question> [--domain NAME] [--model MODEL]\n\nAsk a question against a domain. Falls back to local LLM if server is data-only.",
    "ask-local": "Usage: reasons-service-cli ask-local <question> [--domain NAME] [--model MODEL]\n\nRetrieve from server and synthesize answer with a local LLM.",
    "deep-search": "Usage: reasons-service-cli deep-search <query> [--domain NAME]\n\nDual-path retrieval with IDF ranking across beliefs and source documents.",
    "search": "Usage: reasons-service-cli search <query> [--domain NAME]\n\nSearch beliefs, entries, and source documents.",
    "beliefs": "Usage: reasons-service-cli beliefs [--domain NAME] [--status IN|OUT]\n\nList all beliefs in a domain, optionally filtered by truth value.",
    "domains": "Usage: reasons-service-cli domains\n\nList all available domains.",
    "explain": "Usage: reasons-service-cli explain <belief-id> [--domain NAME]\n\nExplain why a belief is IN or OUT, showing justifications and dependents.",
    "login": "Usage: reasons-service-cli login [--force] [--port PORT]\n\nAuthenticate via MCP OAuth (browser flow). Use --force to re-login.",
    "logout": "Usage: reasons-service-cli logout\n\nClear cached credentials.",
    "status": "Usage: reasons-service-cli status\n\nShow current configuration and authentication status.",
    "init": "Usage: reasons-service-cli init\n\nCreate global config at ~/.config/reasons-service/config.toml.",
    "install-skill": "Usage: reasons-service-cli install-skill [--skill-dir DIR]\n\nInstall the Claude Code skill definition for reasons-service.",
    "import-reasons": "Usage: reasons-service-cli import-reasons <path> --name NAME [--description DESC]\n\nUpload a reasons.db file to create a new domain with beliefs.",
    "mcp": "Usage: reasons-service-cli mcp\n\nStart the MCP server (for Claude Code / Claude Desktop integration).",
}


def _check_help(command: str, args: list[str]) -> bool:
    if "--help" in args or "-h" in args:
        print(HELP.get(command, f"No help available for '{command}'."))
        return True
    return False


def _get_domain(args: list[str]) -> str:
    """Extract --domain from args or fall back to REASONS_PROJECT env var."""
    domain = None
    for flag in ("--domain", "-d", "--project", "-p"):
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                domain = args[idx + 1]
                del args[idx:idx + 2]
            else:
                print(f"Error: {flag} requires a value")
                sys.exit(1)
            break

    if not domain:
        domain = load_config()["project"]

    if not domain:
        print("Error: specify --domain or set REASONS_PROJECT")
        sys.exit(1)

    return client.resolve_domain(domain)


def _get_model(args: list[str]) -> str | None:
    """Extract --model from args."""
    if "--model" in args:
        idx = args.index("--model")
        if idx + 1 < len(args):
            model = args[idx + 1]
            del args[idx:idx + 2]
            return model
    return None


def cmd_ask(args: list[str]):
    if _check_help("ask", args):
        return
    model = _get_model(args)
    domain_id = _get_domain(args)
    question = " ".join(args)
    if not question:
        print("Usage: reasons-service-cli ask <question> [--domain NAME]")
        sys.exit(1)

    try:
        health = client.health()
        if not health.get("llm"):
            print("[server in data-only mode, using local LLM]", file=sys.stderr)
            return _ask_local(domain_id, question, model)
    except Exception:
        pass

    result = client.ask(domain_id, question)
    print(result.get("answer", result.get("compact", result)))


def _ask_local(domain_id: str, question: str, model: str | None):
    """Retrieve from server, synthesize with local LLM."""
    result = client.deep_search(domain_id, question)
    belief_ctx = result.get("belief_context", "")
    chunk_ctx = result.get("chunk_context", "")

    if not belief_ctx and not chunk_ctx:
        print("No matching beliefs or source documents found.")
        return

    from .synthesis import get_model, synthesize, clean_refs, build_sources_section
    resolved_model = get_model(model)
    print(f"[model: {resolved_model}]", file=sys.stderr)
    answer = synthesize(question, belief_ctx, chunk_ctx, model=model)

    beliefs = result.get("beliefs", [])
    sources = result.get("sources", [])
    valid_keys = set()
    for b in beliefs:
        if b.get("cite_key"):
            valid_keys.add(b["cite_key"])
    for s in sources:
        if s.get("cite_key"):
            valid_keys.add(s["cite_key"])
        if s.get("slug"):
            valid_keys.add(s["slug"])
    answer, cited_keys = clean_refs(answer, valid_keys)
    answer += build_sources_section(cited_keys, beliefs, sources)

    print(answer)


def cmd_ask_local(args: list[str]):
    if _check_help("ask-local", args):
        return
    model = _get_model(args)
    domain_id = _get_domain(args)
    question = " ".join(args)
    if not question:
        print("Usage: reasons-service-cli ask-local <question> [--domain NAME] [--model MODEL]")
        sys.exit(1)

    _ask_local(domain_id, question, model)


def cmd_deep_search(args: list[str]):
    if _check_help("deep-search", args):
        return
    domain_id = _get_domain(args)
    query = " ".join(args)
    if not query:
        print("Usage: reasons-service-cli deep-search <query> [--domain NAME]")
        sys.exit(1)

    result = client.deep_search(domain_id, query)

    belief_ctx = result.get("belief_context", "")
    chunk_ctx = result.get("chunk_context", "")
    b_count = result.get("belief_count", 0)
    s_count = result.get("source_count", 0)

    if belief_ctx:
        print(f"=== Beliefs ({b_count}) ===\n")
        print(belief_ctx)

    if chunk_ctx:
        if belief_ctx:
            print()
        print(f"=== Sources ({s_count}) ===\n")
        print(chunk_ctx)

    if not belief_ctx and not chunk_ctx:
        print("No results.")


def cmd_explain(args: list[str]):
    if _check_help("explain", args):
        return
    domain_id = _get_domain(args)
    node_id = " ".join(args)
    if not node_id:
        print("Usage: reasons-service-cli explain <belief-id> [--domain NAME]")
        sys.exit(1)

    belief = client.get_belief(domain_id, node_id)
    explain = client.explain(domain_id, node_id)

    status = belief.get("truth_value", "?")
    print(f"[{status}] {node_id}")
    print(f"  {belief.get('text', '')}")
    if belief.get("source"):
        print(f"  Source: {belief['source']}")
    if belief.get("source_url"):
        print(f"  URL: {belief['source_url']}")

    steps = explain.get("steps", [])
    if steps:
        print(f"\nExplanation:")
        for s in steps:
            print(f"  [{s.get('truth_value', '?')}] {s['node']} — {s.get('reason', '?')}")

    justifications = belief.get("justifications", [])
    if justifications:
        print(f"\nJustifications ({len(justifications)}):")
        for j in justifications:
            jtype = j.get("type", "?")
            label = j.get("label", j.get("id", "?"))
            print(f"  [{jtype}] {label}")

    dependents = belief.get("dependents", [])
    if dependents:
        print(f"\nDependents ({len(dependents)}):")
        for d in dependents:
            dep_id = d if isinstance(d, str) else d.get("id", "?")
            print(f"  {dep_id}")


def cmd_search(args: list[str]):
    if _check_help("search", args):
        return
    domain_id = _get_domain(args)
    query = " ".join(args)
    if not query:
        print("Usage: reasons-service-cli search <query> [--domain NAME]")
        sys.exit(1)

    result = client.search(domain_id, query)

    beliefs = result.get("beliefs", [])
    entries = result.get("entries", [])

    if beliefs:
        print(f"=== Beliefs ({len(beliefs)}) ===")
        for b in beliefs:
            status = b.get("truth_value", "?")
            print(f"  [{status}] {b['text'][:120]}")

    if entries:
        print(f"\n=== Entries ({len(entries)}) ===")
        for e in entries:
            topic = e.get('topic', '?')
            title = e.get('title', '')
            if title and title != topic:
                print(f"  {topic}: {title}")
            else:
                print(f"  {topic}")

    sources = result.get("sources", [])
    if sources:
        print(f"\n=== Sources ({len(sources)}) ===")
        for s in sources:
            label = s.get("source_slug", "?")
            if s.get("section"):
                label += f" / {s['section']}"
            snippet = s.get("snippet", "")[:120]
            print(f"  [{label}] {snippet}")
            if s.get("source_url"):
                print(f"    {s['source_url']}")

    if not beliefs and not entries and not sources:
        print("No results.")


def cmd_beliefs(args: list[str]):
    if _check_help("beliefs", args):
        return
    status = None
    if "--status" in args:
        idx = args.index("--status")
        if idx + 1 < len(args):
            status = args[idx + 1].upper()
            del args[idx:idx + 2]
        else:
            print("Error: --status requires a value (IN or OUT)")
            sys.exit(1)

    domain_id = _get_domain(args)
    result = client.list_beliefs(domain_id, status=status)

    beliefs = result if isinstance(result, list) else result.get("beliefs", result.get("nodes", []))
    if not beliefs:
        print("No beliefs found.")
        return

    for b in beliefs:
        tv = b.get("truth_value", "?")
        node_id = b.get("id", b.get("node_id", "?"))
        text = b.get("text", "")[:100]
        print(f"  [{tv}] {node_id}: {text}")

    print(f"\n{len(beliefs)} beliefs")


def cmd_domains(_args: list[str]):
    if _check_help("domains", _args):
        return
    domains = client.list_domains()
    if not domains:
        print("No domains.")
        return
    print(f"{'Name':<30} {'Description':<35} {'Beliefs':<10} {'Entries':<10} {'Sources':<10}")
    print("-" * 95)
    for d in domains:
        desc = d.get('description', '')[:35]
        print(f"{d['name']:<30} {desc:<35} {d.get('belief_count', '?'):<10} "
              f"{d.get('entry_count', '?'):<10} {d.get('source_count', '?'):<10}")


def cmd_login(args: list[str]):
    if _check_help("login", args):
        return
    from .auth import login
    port = 8085
    force = "--force" in args
    if "--port" in args:
        idx = args.index("--port")
        if idx + 1 < len(args):
            port = int(args[idx + 1])
    login(port=port, force=force)


def cmd_logout(_args: list[str]):
    if _check_help("logout", _args):
        return
    from .auth import TOKEN_FILE
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print("Logged out. Token removed.")
    else:
        print("No cached token.")


def cmd_status(_args: list[str]):
    if _check_help("status", _args):
        return
    from .auth import check_token
    from .config import _find_local_config
    config = load_config()
    url = config.get("url", "")
    if url:
        print(f"URL: {url}")
    else:
        print("URL: not configured (set REASONS_URL or run `reasons-service-cli init`)")
    if config["api_key"]:
        print("Auth: static API key")
    elif not check_token():
        if config.get("anonymous"):
            print("Auth: anonymous (public domains only)")
        else:
            print("Auth: none configured")
    if config["project"]:
        print(f"Default domain: {config['project']}")
    local = _find_local_config()
    if local:
        print(f"Local config: {local}")


def cmd_install_skill(_args: list[str]):
    if _check_help("install-skill", _args):
        return
    from pathlib import Path
    import shutil

    skill_src = Path(__file__).parent.parent / ".claude" / "skills" / "reasons-service" / "SKILL.md"
    if not skill_src.exists():
        skill_src = Path(__file__).parent / "SKILL.md"

    if not skill_src.exists():
        print("Error: SKILL.md not found in package")
        sys.exit(1)

    skill_dir = None
    if "--skill-dir" in _args:
        idx = _args.index("--skill-dir")
        if idx + 1 < len(_args):
            skill_dir = Path(_args[idx + 1])

    if not skill_dir:
        skill_dir = Path.home() / ".claude" / "skills" / "reasons-service"

    skill_dir.mkdir(parents=True, exist_ok=True)
    dest = skill_dir / "SKILL.md"
    shutil.copy2(skill_src, dest)
    print(f"Skill installed: {dest}")


def cmd_import_reasons(args: list[str]):
    if _check_help("import-reasons", args):
        return
    name = None
    description = ""

    if "--name" in args:
        idx = args.index("--name")
        if idx + 1 < len(args):
            name = args[idx + 1]
            del args[idx:idx + 2]
        else:
            print("Error: --name requires a value")
            sys.exit(1)

    if "--description" in args:
        idx = args.index("--description")
        if idx + 1 < len(args):
            description = args[idx + 1]
            del args[idx:idx + 2]
        else:
            print("Error: --description requires a value")
            sys.exit(1)

    if not args:
        print("Usage: reasons-service-cli import-reasons <path/to/reasons.db> --name NAME [--description DESC]")
        sys.exit(1)

    db_path = args[0]

    import os.path
    if not os.path.isfile(db_path):
        print(f"Error: file not found: {db_path}")
        sys.exit(1)

    if not name:
        name = os.path.basename(os.path.dirname(os.path.abspath(db_path)))
        if not name or name == ".":
            name = os.path.splitext(os.path.basename(db_path))[0]

    result = client.import_reasons(db_path, name, description)
    print(f"Domain created: {result['name']}")
    print(f"  ID: {result['domain_id']}")
    print(f"  Beliefs: {result['beliefs']}")
    print(f"  Nogoods: {result['nogoods']}")


def cmd_init(_args: list[str]):
    if _check_help("init", _args):
        return
    from .config import init_config
    init_config()


def cmd_mcp(_args: list[str]):
    if _check_help("mcp", _args):
        return
    from .mcp_server import main
    main()


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    command = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "ask": cmd_ask,
        "ask-local": cmd_ask_local,
        "deep-search": cmd_deep_search,
        "beliefs": cmd_beliefs,
        "explain": cmd_explain,
        "search": cmd_search,
        "domains": cmd_domains,
        "login": cmd_login,
        "logout": cmd_logout,
        "status": cmd_status,
        "init": cmd_init,
        "install-skill": cmd_install_skill,
        "import-reasons": cmd_import_reasons,
        "mcp": cmd_mcp,
    }

    if command in commands:
        try:
            commands[command](args)
        except ValueError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
