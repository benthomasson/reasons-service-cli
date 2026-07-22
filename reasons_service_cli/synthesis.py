"""Local LLM synthesis for reasons-service-cli ask-local.

Takes pre-retrieved context from deep_search and synthesizes an answer
using a local LLM via CLI subprocess (claude, gemini, or ollama).
"""

import os
import re
import shutil
import subprocess

from .config import load_config

_PROMPT = """\
You are an expert assistant answering questions using a curated knowledge base.
You have two sources of context:

1. **TMS Beliefs** — verified facts from a Truth Maintenance System, each with a truth value (IN = accepted, OUT = retracted) and a belief ID. Prefer IN beliefs. Cite beliefs by their ID in square brackets, e.g. [ec2-pay-per-instance-second].

2. **Source Documents** — relevant passages from source documents, each with a source slug. Cite sources by their slug in square brackets, e.g. [ec2-instance-types].

Rules:
- Answer the question comprehensively using ONLY the provided context
- If beliefs and sources disagree, prefer beliefs (they have been through truth maintenance)
- Cite your sources inline using [belief-id] or [source-slug]
- Structure your answer with clear headings and bullet points
- If the context does not contain enough information, say so explicitly
- Do not use knowledge outside the provided context

## Question

{question}

## TMS Beliefs

{beliefs}

## Source Documents

{sources}

---

Answer the question using the context above. Cite sources inline."""


_MODEL_COMMANDS = {
    "claude": ["claude", "-p"],
    "gemini": ["gemini", "--skip-trust", "-p", ""],
}


def _resolve_cmd(model: str) -> list[str]:
    """Resolve a model name to a CLI command list."""
    if model in _MODEL_COMMANDS:
        return _MODEL_COMMANDS[model]
    if model.startswith("claude:"):
        submodel = model.split(":", 1)[1]
        return ["claude", "-p", "--model", submodel]
    if model.startswith("gemini:"):
        submodel = model.split(":", 1)[1]
        return ["gemini", "--skip-trust", "-m", submodel, "-p", ""]
    if model.startswith("ollama:"):
        ollama_model = model.split(":", 1)[1]
        return ["ollama", "run", ollama_model]
    available = list(_MODEL_COMMANDS) + ["claude:<model>", "gemini:<model>", "ollama:<model>"]
    raise ValueError(f"Unknown model: {model}. Available: {available}")


def _get_model(model: str | None) -> str:
    """Resolve model name from arg, config, or default."""
    if model:
        return model
    config = load_config()
    return config.get("llm", "") or os.getenv("REASONS_LLM_MODEL", "") or "claude"


def get_model(model: str | None = None) -> str:
    """Resolve model name from arg, config, or default."""
    return _get_model(model)


def clean_refs(text: str, valid_keys: set[str]) -> tuple[str, set[str]]:
    """Remove hallucinated refs and return (cleaned_text, cited_keys)."""
    cited = set()

    def _replace(m):
        key = m.group(1)
        end = m.end()
        if end < len(text) and text[end] == '(':
            return m.group(0)
        if key in valid_keys:
            cited.add(key)
            return m.group(0)
        if key in ('x', ' ', '!') or key.startswith('^'):
            return m.group(0)
        return ''

    cleaned = re.sub(r'\[([^\]]+)\]', _replace, text)
    return cleaned, cited


def build_sources_section(cited_keys: set[str], beliefs: list[dict],
                          sources: list[dict]) -> str:
    """Build a Sources/Beliefs section from cited keys and deep-search metadata."""
    cited_beliefs = [b for b in beliefs if b.get("cite_key") in cited_keys]
    cited_sources = [s for s in sources if s.get("cite_key") in cited_keys
                     or s.get("slug") in cited_keys]

    lines = []

    if cited_sources:
        lines.append("\n\n## Sources\n")
        for s in cited_sources:
            label = s.get("label", s.get("slug", ""))
            key = s.get("cite_key", s.get("slug", ""))
            url = s.get("url", "")
            if url:
                lines.append(f"- **[{key}]** {label} — [source]({url})")
            else:
                lines.append(f"- **[{key}]** {label}")

    if cited_beliefs:
        lines.append("\n\n## Beliefs\n")
        for b in cited_beliefs:
            key = b.get("cite_key", "")
            label = b.get("label", "")
            lines.append(f"- **[{key}]** {label}")

    return "\n".join(lines)


def synthesize(question: str, beliefs: str, sources: str, model: str | None = None) -> str:
    """Synthesize an answer from deep_search context using a local LLM."""
    model = _get_model(model)
    cmd = _resolve_cmd(model)
    binary = cmd[0]
    if not shutil.which(binary):
        raise FileNotFoundError(f"'{binary}' CLI not found in PATH. Install it or set --model.")

    prompt = _PROMPT.format(
        question=question,
        beliefs=beliefs or "(none found)",
        sources=sources or "(none found)",
    )

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{model} failed: {result.stderr}")

    output = result.stdout
    if model.startswith("ollama:") and "Thinking...\n" in output:
        parts = output.split("...done thinking.\n", 1)
        if len(parts) == 2:
            output = parts[1]
    return output
