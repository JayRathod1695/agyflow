"""Agent prompt assembly, payload extraction, and error classification."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from agyflow.lib.contract import (
    E_AUTH,
    E_CLI,
    E_EMPTY,
    E_POLICY,
    E_PROVIDER,
    E_RATE_LIMIT,
    E_SCHEMA,
    E_TIMEOUT,
    EXIT_FAILED,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    EXIT_RETRYABLE,
)
from agyflow.lib.proc import ProcResult
from agyflow.lib.schema import canonical_json, validate_data


def render_template(template: str, vars_dict: dict[str, Any]) -> str:
    """Replace {{name}} placeholders in template with formatted values."""
    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        if key not in vars_dict:
            raise KeyError(f"Unknown placeholder '{{{{{key}}}}}'")
        val = vars_dict[key]
        if isinstance(val, (list, tuple)):
            if key in ("forbidden", "forbidden_paths"):
                return ", ".join(str(x) for x in val)
            return " ".join(str(x) for x in val)
        return str(val)

    return re.sub(r"\{\{([a-zA-Z0-9_]+)\}\}", replacer, template)


def parse_prompt_sections(content: str) -> dict[str, str]:
    """Extract XML-style tagged sections from a prompt markdown file."""
    sections: dict[str, str] = {}
    tags = ["role", "rules", "task", "example"]
    for tag in tags:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            sections[tag] = match.group(1).strip()
    return sections


def assemble_prompt(
    shared_rules: str,
    role_content: str,
    inputs: dict[str, Any],
    vars_dict: dict[str, Any],
    inline_max_bytes: int = 6000,
    retry_info: dict[str, Any] | None = None,
) -> str:
    """Assemble final agent prompt according to Section 9.2."""
    rendered_shared = render_template(shared_rules, vars_dict)
    rendered_role = render_template(role_content, vars_dict)
    sec = parse_prompt_sections(rendered_role)

    parts: list[str] = []
    if "role" in sec:
        parts.append(f"<role>{sec['role']}</role>")

    role_rules = sec.get("rules", "")
    parts.append(f"<rules>\n{rendered_shared}\n{role_rules}\n</rules>")

    parts.append("<inputs>")
    for name in sorted(inputs.keys()):
        val = inputs[name]
        raw_json = canonical_json(val)
        b_len = len(raw_json.encode("utf-8"))
        if b_len <= inline_max_bytes:
            parts.append(f'<input name="{name}" format="json">{raw_json}</input>')
        else:
            parts.append(f'<input name="{name}" format="json" mode="path" bytes="{b_len}"/>')
    parts.append("</inputs>")

    if retry_info:
        attempt = retry_info.get("attempt", 1)
        last_code = retry_info.get("last_code", "")
        errors = retry_info.get("errors", [])
        feedback = f"Attempt {attempt} failed ({last_code}). " + " ".join(errors[:5])
        parts.append(f"<retry_feedback>{feedback}</retry_feedback>")

    if "task" in sec:
        parts.append(f"<task>\n{sec['task']}\n</task>")
    if "example" in sec:
        parts.append(f"<example>\n{sec['example']}\n</example>")

    prompt = "\n".join(parts)
    if len(prompt.encode("utf-8")) > 100_000:
        raise ValueError("Prompt exceeds 100KB limit; reduce inputs")
    return prompt


def extract_payload(stdout: str, schema: dict[str, Any] | str | None = None) -> dict[str, Any] | None:
    """Extract JSON payload from model output tolerant to chat preambles and wrappers (Section 9.4)."""
    text = stdout.strip()
    if not text:
        return None

    # 1. Try direct JSON parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            if schema and not validate_data(obj, schema):
                return obj
            for key in ["result", "response", "text", "output", "content", "message"]:
                if key in obj and isinstance(obj[key], str):
                    text = obj[key].strip()
                    break
            else:
                if not schema:
                    return obj
    except Exception:
        pass

    # 2. Strip code fences
    fence_pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
    match = re.search(fence_pattern, text)
    if match:
        text = match.group(1).strip()

    # 3. Find largest balanced {...} block
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return None


def classify_agent_error(proc_res: ProcResult) -> tuple[int, str, str]:
    """Classify agent invocation errors into (contract_exit_code, reason_code, message) (Section 9.4)."""
    if proc_res.timed_out:
        return EXIT_RETRYABLE, E_TIMEOUT, "agent process timed out"

    err_text = proc_res.stderr.lower()
    if re.search(r"429|rate\s*limit|quota|resource_exhausted|overloaded", err_text):
        return EXIT_RETRYABLE, E_RATE_LIMIT, "provider rate limit or quota exceeded"
    if re.search(r"timed\s*out|deadline|print-timeout", err_text):
        return EXIT_RETRYABLE, E_TIMEOUT, "agent request timed out"
    if re.search(r"401|403|unauthenticated|not\s*logged\s*in|login\s*required|permission\s*denied", err_text):
        return EXIT_NEEDS_HUMAN, E_AUTH, "agent authentication failure or login required"
    if "unknown flag" in err_text or "not found" in err_text:
        return EXIT_FAILED, E_CLI, "agent CLI command or flag error"

    if proc_res.returncode != 0:
        return EXIT_RETRYABLE, E_PROVIDER, f"agent exited with non-zero code {proc_res.returncode}"

    if not proc_res.stdout.strip():
        return EXIT_RETRYABLE, E_EMPTY, "agent produced empty stdout"

    return EXIT_OK, "", "ok"


SUPPORTED_CAPABILITIES: dict[str, bool] = {
    "stream_guard": True,
    "stream_json_tool_events": True,
}


def has_capability(cap: str) -> bool:
    """Return whether a given agentlib/launcher capability is supported."""
    return SUPPORTED_CAPABILITIES.get(cap, False)
