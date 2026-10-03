"""Model Context Protocol server: let Claude (Desktop or Code) use Chart Cleaner.

Runs over stdio only — no network port is opened. Tools wrap
:mod:`chartcleaner.service`, so results match the app exactly:

* ``clean_chart`` — full clean (redaction, chrome removal, abbreviations…)
* ``abbreviate`` / ``expand_abbreviations``
* ``render_prompt`` — clean, then wrap in a prompt template
* ``list_presets`` / ``list_prompt_templates``
* ``ask_chart`` — grounded question via the on-device LLM (Ollama)

Start it with ``clean-chart-mcp`` (``clean-chart-mcp.cmd`` on Windows); see
docs/integrations.md for Claude Desktop / Claude Code setup.
"""

from __future__ import annotations

from typing import Any

from . import __version__, service, store
from .prompt_templates import templates

__all__ = ["build_server", "main"]

INSTRUCTIONS = (
    "Chart Cleaner cleans Epic-style medical chart text on this computer: it removes EMR "
    "chrome and boilerplate, redacts structured PHI per the user's rules, and can shorten or "
    "expand medical abbreviations. Clean a chart before summarizing or writing notes from it. "
    "Redaction follows the user's configuration and is not a guarantee of de-identification; "
    "treat output as clinical data."
)


def clean_chart(text: str, preset: str = "", format: str = "text", delta: bool = False) -> dict[str, Any]:
    """Clean a chart exactly like the Chart Cleaner app.

    Args:
        text: Raw chart text (an Epic export or copied note).
        preset: Optional saved preset name (see list_presets); empty uses current rules.
        format: "text", "markdown", "json" or "xml" (sectioned for LLMs).
        delta: Keep only what changed between daily notes (copy-forward removed).
    """
    out = service.clean(text, preset=preset or None, fmt=format, delta=delta, source="api:mcp")
    return {k: out[k] for k in ("text", "summary", "reduction", "phi", "warnings") if k in out}


def abbreviate(text: str, preset: str = "") -> dict[str, Any]:
    """Shorten full medical terms using the user's abbreviation dictionary (nothing else changes)."""
    out = service.abbreviate(text, preset=preset or None, source="api:mcp")
    return {"text": out["text"], "replacements": out["replacements"]}


def expand_abbreviations(text: str, preset: str = "") -> dict[str, Any]:
    """Spell abbreviations out in full. Abbreviations with several meanings are left as
    written and listed under "ambiguous"."""
    out = service.expand(text, preset=preset or None, source="api:mcp")
    return {"text": out["text"], "expansions": out["expansions"], "ambiguous": out["ambiguous"]}


def render_prompt(text: str, template: str, preset: str = "") -> str:
    """Clean a chart and wrap it in one of the user's prompt templates (see
    list_prompt_templates), e.g. "Progress note" or "Sign-out / handoff"."""
    return service.prompt(text, template, preset=preset or None, source="api:mcp")["text"]


def list_presets() -> list[str]:
    """Names of the user's saved rule presets."""
    return store.list_presets()


def list_prompt_templates() -> list[dict[str, str]]:
    """Prompt templates available to render_prompt."""
    try:
        cfg = service.load_active_config()
    except Exception:
        cfg = {}
    return [{"name": t["name"], "format": t["format"]} for t in templates(cfg)]


def ask_chart(text: str, question: str) -> dict[str, Any]:
    """Answer a question about a chart with the on-device LLM (Ollama), with a grounding
    score showing how much of the answer is supported by the chart."""
    return service.ask(question, text)


TOOLS = (clean_chart, abbreviate, expand_abbreviations, render_prompt, list_presets,
         list_prompt_templates, ask_chart)


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as ex:  # pragma: no cover - depends on the installed SDK
        raise SystemExit("The MCP server needs the 'mcp' package: run the installer again "
                         "(or .venv/bin/pip install 'mcp>=2').") from ex
    server = MCPServer("Chart Cleaner", version=__version__, instructions=INSTRUCTIONS)
    for fn in TOOLS:
        server.tool()(fn)
    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
