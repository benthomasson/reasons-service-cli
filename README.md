# reasons-service-cli

CLI client for [reasons-service](https://github.com/benthomasson/reasons-service) — ask questions, search beliefs, and query domain knowledge bases from the terminal or Claude Code.

The CLI command is `reasons-service-cli`.

## Install

```bash
pip install reasons-service-cli
```

Or with uv:

```bash
uv tool install reasons-service-cli
```

## Quick Start

Create a config pointing to your reasons-service instance:

```bash
reasons-service-cli init
# Edit ~/.config/reasons-service/config.toml with your settings
```

Then query:

```bash
reasons-service-cli ask "What is EEM?"
reasons-service-cli search "epistemic memory"
reasons-service-cli explain eem-definition
```

## Configuration

Config file: `~/.config/reasons-service/config.toml`

```toml
[default]
url = "http://localhost:8000"
project = "my-domain"
# api_key = "your-api-key"
```

For Google OAuth:

```toml
[default]
url = "http://localhost:8000"
project = "my-domain"
google_client_id = "your-id.apps.googleusercontent.com"
google_client_secret = "your-secret"
```

Environment variables override config file values, CLI flags override both:

| Config Key | Environment Variable | Description |
|---|---|---|
| `url` | `REASONS_URL` | reasons-service base URL |
| `api_key` | `REASONS_API_KEY` | Static API key (alternative to OAuth) |
| `project` | `REASONS_PROJECT` | Default domain name |
| `anonymous` | `REASONS_ANONYMOUS` | Skip auth for public domains |
| `google_client_id` | `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `google_client_secret` | `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |

## Commands

### `reasons-service-cli ask <question>`

Ask a question and get a complete answer. Uses dual-path retrieval (TMS beliefs + full-text search). When the server is in data-only mode, automatically falls back to local LLM synthesis.

```bash
reasons-service-cli ask "What is EEM?"
reasons-service-cli ask "How does the TMS work?" --domain my-domain
```

### `reasons-service-cli search <query>`

Keyword search over beliefs and entries. No LLM involved — fast full-text search.

```bash
reasons-service-cli search "epistemic memory"
reasons-service-cli search "truth maintenance"
```

### `reasons-service-cli domains`

List all available domains with belief, entry, and source counts.

### `reasons-service-cli explain <belief-id>`

Explain why a belief is IN or OUT, showing its justification chain.

```bash
reasons-service-cli explain eem-definition --domain my-domain
```

### `reasons-service-cli login`

Authenticate via Google OAuth. Opens a browser, exchanges an auth code via localhost callback, and caches the token at `~/.config/reasons-service/token.json`. Tokens auto-refresh on subsequent calls.

```bash
reasons-service-cli login
reasons-service-cli login --port 9090
```

### `reasons-service-cli logout`

Clear cached OAuth credentials.

### `reasons-service-cli status`

Show current authentication state, service URL, and default domain.

### `reasons-service-cli init`

Create a default config file at `~/.config/reasons-service/config.toml`.

## Authentication

Three modes:

1. **Anonymous** — access public domains. Set `anonymous = true` in config.

2. **Google OAuth** — run `reasons-service-cli login`, tokens are cached and auto-refreshed.

3. **Static API key** — set `api_key` in config or `REASONS_API_KEY` env var.

## Claude Code Skill

Install the `/reasons-service` skill for use in Claude Code sessions:

```bash
reasons-service-cli install-skill
```

Then in Claude Code:

```
/reasons-service ask what is EEM?
/reasons-service search epistemic memory
/reasons-service explain eem-definition
```

## Architecture

```
Claude Code / Terminal
       |
   reasons-service-cli (httpx)
       |
reasons-service (FastAPI)
  +-- TMS beliefs (PostgreSQL)
  +-- FTS sources (tsvector)
```

## License

MIT
