---
name: reasons-service
description: Query reasons-service knowledge bases — ask questions, search beliefs, list domains
argument-hint: "[ask|search|domains|login|status] [args...]"
allowed-tools: Bash(reasons-service-cli *), Read
---

You are querying reasons-service knowledge bases using the `reasons-service-cli` CLI tool. Reasons-service is a domain expert system backed by a Truth Maintenance System (TMS) for beliefs and full-text search over source documents.

## Why Use This Tool

Reasons-service maintains curated knowledge bases with:
- **TMS beliefs** — facts with truth values (IN/OUT), justifications, and retraction cascades
- **Source documents** — chunked and indexed for full-text search with IDF-weighted re-ranking

The `reasons-service-cli` CLI is a thin HTTP client that connects to a running reasons-service instance. It handles authentication (Google OAuth or API key) and domain resolution automatically.

## How to Run

```bash
reasons-service-cli $ARGUMENTS
```

## Setup

```bash
reasons-service-cli init                    # creates ~/.config/reasons-service/config.toml
reasons-service-cli login                   # Google OAuth browser login
```

Config file (`~/.config/reasons-service/config.toml`):
```toml
url = "http://localhost:8000"
project = "my-domain"
google_client_id = "your-id.apps.googleusercontent.com"
google_client_secret = "your-secret"
```

## Subcommand Behavior

### `ask <question> [--domain NAME] [--model MODEL]`
Ask a question and get a complete answer. Uses dual-path retrieval (TMS beliefs + FTS source search). Returns the full answer text.

Convert natural language to CLI arguments:
- `/reasons-service what is EEM?` → `reasons-service-cli ask "What is EEM?"`
- `/reasons-service search pipeline risks` → `reasons-service-cli search "pipeline risks"`

```bash
reasons-service-cli ask "What is the current state of the project?"
reasons-service-cli ask "What are the key risks?" --domain my-domain
```

### `search <query> [--domain NAME]`
Search beliefs and entries by keyword. Returns matching beliefs (with IN/OUT status) and entry titles. Faster than `ask` — no LLM involved, just full-text search.

```bash
reasons-service-cli search "pipeline" --domain my-domain
reasons-service-cli search "contradiction"
```

### `domains`
List all available domains with belief/entry/source counts.

```bash
reasons-service-cli domains
```

### `explain <belief-id> [--domain NAME]`
Explain why a belief is IN or OUT, showing its justification chain.

```bash
reasons-service-cli explain my-belief-id --domain my-domain
```

### `login [--port PORT]`
Authenticate via Google OAuth. Opens a browser, caches the token at `~/.config/reasons-service/token.json`. Auto-refreshes on subsequent calls.

### `logout`
Clear cached credentials.

### `status`
Show current authentication state, URL, and default domain.

### `init`
Create a default config file at `~/.config/reasons-service/config.toml`.

## When to Use Which Command

| Need | Command |
|------|---------|
| Answer a question with reasoning | `reasons-service-cli ask "..."` |
| Find beliefs/entries by keyword | `reasons-service-cli search "..."` |
| See what domains exist | `reasons-service-cli domains` |
| Check auth is working | `reasons-service-cli status` |

## After Any Command

- If the command returned results, summarize them concisely
- If `ask` returned an answer, present it to the user — don't just say "the answer was returned"
- If `search` returned beliefs, note which are IN vs OUT
- If authentication fails, suggest `reasons-service-cli login`
- Keep responses concise — the tool output speaks for itself
