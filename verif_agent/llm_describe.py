"""LLM-based semantic description generator for the parsed Design.

design.json has descriptive fields — `function` / interface `role`s /
`related_testbench_note` / `vcs_compatibility_note` — that require understanding
what the DUT *does*, not just its syntax. Regex/templates can only emit canned
phrases ("AXI4 sub-system"); an LLM can write "AXI4 read data-width adapter".

Configuration is read from a mounted .env (python-dotenv):
    OPENAI_BASE_URL = https://...   (any OpenAI-compatible endpoint)
    OPENAI_API_KEY  = sk-...
    LLM_MODEL       = <model name the endpoint supports>

If .env is absent or the call fails, describe() returns None and the reporter
falls back to placeholders — the pipeline never breaks on LLM unavailability.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)


def _load_env() -> None:
    """Load .env from the cwd (/work in the container) if dotenv is installed."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:                                  # noqa: BLE001
        pass


def _client():
    """Build an OpenAI client from env vars. Returns None if unconfigured."""
    _load_env()
    base_url = os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not base_url or not api_key:
        return None
    try:
        from openai import OpenAI
        return OpenAI(base_url=base_url, api_key=api_key)
    except Exception as exc:                           # noqa: BLE001
        log.warning("LLM client init failed: %s", exc)
        return None


def _build_prompt(design) -> tuple[str, str]:
    ports = "\n".join(
        f"  {p.direction:6s} [{p.width:3d}] {p.name}  (group={p.protocol_group})"
        for p in design.ports
    ) or "  (none)"
    params = ", ".join(f"{p.name}={p.value}" for p in design.parameters) or "(none)"
    proto = design.primary_protocol or "(unrecognized)"
    user = (
        f"You are analyzing a Verilog DUT to document it for a verification environment.\n"
        f"Top module: {design.top}\n"
        f"Inferred protocol: {proto}\n"
        f"Parameters: {params}\n"
        f"Ports:\n{ports}\n\n"
        f"Describe this DUT. Reply with ONLY a JSON object (no prose, no markdown), "
        f"exactly this shape:\n"
        f'{{\n'
        f'  "function": "<one line: what this DUT does>",\n'
        f'  "interfaces": {{\n'
        f'    "<iface_name>": {{"role": "<slave|master and which side, e.g. slave/read requester side>"}}\n'
        f'  }},\n'
        f'  "related_testbench_note": {{"finding": "<one sentence>"}},\n'
        f'  "vcs_compatibility_note": {{"finding": "<one sentence, or empty string>"}}\n'
        f'}}'
    )
    system = (
        "You are a hardware verification engineer. "
        "Output strictly valid JSON only — no markdown fences, no commentary."
    )
    return system, user


def _strip_fences(text: str) -> str:
    """Remove ```...``` / ```json...``` fences if the model added them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def describe(design) -> dict | None:
    """Return an LLM-generated description dict, or None on any failure."""
    client = _client()
    model = os.environ.get("LLM_MODEL")
    if client is None or not model:
        log.info("LLM describe skipped (set OPENAI_BASE_URL/OPENAI_API_KEY/LLM_MODEL in .env)")
        return None
    system, user = _build_prompt(design)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
        )
        content = _strip_fences(resp.choices[0].message.content or "")
        return json.loads(content)
    except Exception as exc:                           # noqa: BLE001
        log.warning("LLM describe failed, using placeholders: %s", exc)
        return None
