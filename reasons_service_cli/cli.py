import argparse
import sys

from . import client
from .config import load_config


def _resolve_domain(args: argparse.Namespace) -> str:
    domain = getattr(args, "domain", None)
    if not domain:
        domain = load_config()["project"]
    if not domain:
        print("Error: specify --domain or set REASONS_PROJECT")
        sys.exit(1)
    return client.resolve_domain(domain)


def cmd_ask(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    question = " ".join(args.question)

    try:
        health = client.health()
        if not health.get("llm"):
            print("[server in data-only mode, using local LLM]", file=sys.stderr)
            return _ask_local(domain_id, question, args.model)
    except Exception:
        pass

    result = client.ask(domain_id, question)
    print(result.get("answer", result.get("compact", result)))


def _ask_local(domain_id: str, question: str, model: str | None):
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


def cmd_ask_local(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    question = " ".join(args.question)
    _ask_local(domain_id, question, args.model)


def cmd_deep_search(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    query = " ".join(args.query)

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


def cmd_show(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    node_id = " ".join(args.belief_id)

    belief = client.get_belief(domain_id, node_id)
    if belief.get("error"):
        print(f"Error: {belief['error']}")
        sys.exit(1)

    tv = belief.get("truth_value", "?")
    print(f"[{tv}] {node_id}")
    print(f"  {belief.get('text', '')}")

    if belief.get("source"):
        print(f"  Source: {belief['source']}")
    if belief.get("source_url"):
        print(f"  URL: {belief['source_url']}")

    meta = belief.get("metadata", {})
    if meta:
        for k, v in meta.items():
            print(f"  {k}: {v}")

    if belief.get("created_at"):
        print(f"  Created: {belief['created_at']}")
    if belief.get("updated_at") and belief["updated_at"] != belief.get("created_at"):
        print(f"  Updated: {belief['updated_at']}")
    if belief.get("reviewed_at"):
        print(f"  Reviewed: {belief['reviewed_at']}")
    if belief.get("verified_at"):
        print(f"  Verified: {belief['verified_at']}")
    if belief.get("retracted_at"):
        print(f"  Retracted: {belief['retracted_at']}")

    justifications = belief.get("justifications", [])
    if justifications:
        print(f"\nJustifications ({len(justifications)}):")
        for j in justifications:
            jtype = j.get("type", "?")
            label = j.get("label", "")
            print(f"  [{jtype}] {label}")
            for a in j.get("antecedents", []):
                print(f"    + {a}")
            for o in j.get("outlist", []):
                print(f"    - {o}")

    dependents = belief.get("dependents", [])
    if dependents:
        print(f"\nDependents ({len(dependents)}):")
        for d in dependents:
            dep_id = d if isinstance(d, str) else d.get("id", "?")
            print(f"  {dep_id}")


def cmd_explain(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    node_id = " ".join(args.belief_id)

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


def cmd_search(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    query = " ".join(args.query)

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


def cmd_beliefs(args: argparse.Namespace):
    domain_id = _resolve_domain(args)
    status = args.status.upper() if args.status else None
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


def cmd_domains(args: argparse.Namespace):
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


def cmd_login(args: argparse.Namespace):
    from .auth import login
    login(port=args.port, force=args.force)


def cmd_logout(args: argparse.Namespace):
    from .auth import TOKEN_FILE
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print("Logged out. Token removed.")
    else:
        print("No cached token.")


def cmd_status(args: argparse.Namespace):
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


def cmd_install_skill(args: argparse.Namespace):
    from pathlib import Path
    import shutil

    skill_src = Path(__file__).parent.parent / ".claude" / "skills" / "reasons-service" / "SKILL.md"
    if not skill_src.exists():
        skill_src = Path(__file__).parent / "SKILL.md"

    if not skill_src.exists():
        print("Error: SKILL.md not found in package")
        sys.exit(1)

    skill_dir = Path(args.skill_dir) if args.skill_dir else Path.home() / ".claude" / "skills" / "reasons-service"
    skill_dir.mkdir(parents=True, exist_ok=True)
    dest = skill_dir / "SKILL.md"
    shutil.copy2(skill_src, dest)
    print(f"Skill installed: {dest}")


def cmd_import_reasons(args: argparse.Namespace):
    import os.path

    db_path = args.path
    if not os.path.isfile(db_path):
        print(f"Error: file not found: {db_path}")
        sys.exit(1)

    name = args.name
    if not name:
        name = os.path.basename(os.path.dirname(os.path.abspath(db_path)))
        if not name or name == ".":
            name = os.path.splitext(os.path.basename(db_path))[0]

    result = client.import_reasons(db_path, name, args.description or "")
    print(f"Domain created: {result['name']}")
    print(f"  ID: {result['domain_id']}")
    print(f"  Beliefs: {result['beliefs']}")
    print(f"  Nogoods: {result['nogoods']}")


def cmd_init(args: argparse.Namespace):
    from .config import init_config
    init_config()


def cmd_mcp(args: argparse.Namespace):
    from .mcp_server import main as mcp_main
    mcp_main()


EPILOG = """\
Config priority (highest wins):
  CLI flags > env vars > .reasons-service.toml (local) > ~/.config/reasons-service/config.toml (global)

Local config (.reasons-service.toml in repo root):
  project = "my-domain"

Global config (~/.config/reasons-service/config.toml):
  [default]
  url = "https://reasons.example.com"
  project = "my-domain"
"""


def main():
    parser = argparse.ArgumentParser(
        prog="reasons-service-cli",
        description="CLI client for reasons-service — ask questions, search beliefs, list domains",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    domain_parent = argparse.ArgumentParser(add_help=False)
    domain_parent.add_argument("--domain", "-d", help="domain name or ID")
    domain_parent.add_argument("--project", "-p", dest="domain", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("ask", parents=[domain_parent], help="Ask a question against a domain")
    p.add_argument("question", nargs="+", help="question text")
    p.add_argument("--model", help="LLM model to use")

    p = sub.add_parser("ask-local", parents=[domain_parent], help="Ask using local LLM for synthesis")
    p.add_argument("question", nargs="+", help="question text")
    p.add_argument("--model", help="local LLM model to use")

    p = sub.add_parser("deep-search", parents=[domain_parent], help="Dual-path retrieval with IDF ranking")
    p.add_argument("query", nargs="+", help="search query")

    p = sub.add_parser("search", parents=[domain_parent], help="Search beliefs, entries, and sources")
    p.add_argument("query", nargs="+", help="search query")

    p = sub.add_parser("beliefs", parents=[domain_parent], help="List beliefs in a domain")
    p.add_argument("--status", choices=["IN", "OUT", "in", "out"], help="filter by truth value")

    sub.add_parser("domains", help="List all available domains")

    p = sub.add_parser("show", parents=[domain_parent], help="Show full details for a belief")
    p.add_argument("belief_id", nargs="+", help="belief node ID")

    p = sub.add_parser("explain", parents=[domain_parent], help="Explain why a belief is IN or OUT")
    p.add_argument("belief_id", nargs="+", help="belief node ID")

    p = sub.add_parser("login", help="Authenticate via MCP OAuth (browser flow)")
    p.add_argument("--force", action="store_true", help="re-login even if already authenticated")
    p.add_argument("--port", type=int, default=8085, help="local callback port (default: 8085)")

    sub.add_parser("logout", help="Clear cached credentials")
    sub.add_parser("status", help="Show configuration and authentication status")
    sub.add_parser("init", help="Create global config at ~/.config/reasons-service/config.toml")

    p = sub.add_parser("install-skill", help="Install Claude Code skill definition")
    p.add_argument("--skill-dir", help="target directory for SKILL.md")

    p = sub.add_parser("import-reasons", help="Upload a reasons.db to create a domain")
    p.add_argument("path", help="path to reasons.db file")
    p.add_argument("--name", help="domain name (default: parent directory name)")
    p.add_argument("--description", help="domain description")

    sub.add_parser("mcp", help="Start the MCP server")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "ask": cmd_ask,
        "ask-local": cmd_ask_local,
        "deep-search": cmd_deep_search,
        "beliefs": cmd_beliefs,
        "show": cmd_show,
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

    try:
        commands[args.command](args)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
