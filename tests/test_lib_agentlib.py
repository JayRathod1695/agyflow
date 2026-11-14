"""Unit tests for src/agyflow/lib/agentlib.py."""

from pathlib import Path
import pytest
from agyflow.lib.agentlib import (
    assemble_prompt,
    classify_agent_error,
    extract_payload,
    render_template,
)
from agyflow.lib.contract import (
    E_AUTH,
    E_EMPTY,
    E_RATE_LIMIT,
    E_TIMEOUT,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    EXIT_RETRYABLE,
)
from agyflow.lib.proc import ProcResult


def test_render_template() -> None:
    tpl = "Cap: {{cap}}, Forbidden: {{forbidden}}"
    res = render_template(tpl, {"cap": 10, "forbidden": ["a", "b"]})
    assert res == "Cap: 10, Forbidden: a, b"


def test_assemble_prompt() -> None:
    shared = "Be concise. Cap is {{cap}}."
    role = "<role>Reviewer</role>\n<rules>Check code.</rules>\n<task>Find bugs.</task>"
    prompt = assemble_prompt(
        shared_rules=shared,
        role_content=role,
        inputs={"snap": {"head": "123"}},
        vars_dict={"cap": 20},
    )
    assert "<role>Reviewer</role>" in prompt
    assert "<inputs>" in prompt
    assert '<input name="snap" format="json">{"head":"123"}</input>' in prompt
    assert "<task>\nFind bugs.\n</task>" in prompt


def test_extract_payload() -> None:
    # Direct JSON
    assert extract_payload('{"key": "val"}') == {"key": "val"}

    # Wrapped in markdown fences
    fenced = "Here is the result:\n```json\n{\"id\": \"F-001\"}\n```\nHope it helps!"
    assert extract_payload(fenced) == {"id": "F-001"}

    # Chat wrapper
    chat = '{"response": "{\\"id\\": \\"F-002\\"}"}'
    assert extract_payload(chat) == {"id": "F-002"}

    # Empty
    assert extract_payload("") is None


def test_classify_agent_error() -> None:
    r_timeout = ProcResult(returncode=None, stdout="", stderr="", timed_out=True, duration_s=10.0)
    code, reason, _ = classify_agent_error(r_timeout)
    assert code == EXIT_RETRYABLE
    assert reason == E_TIMEOUT

    r_rate = ProcResult(returncode=1, stdout="", stderr="Error 429: rate limit exceeded", timed_out=False, duration_s=1.0)
    code, reason, _ = classify_agent_error(r_rate)
    assert code == EXIT_RETRYABLE
    assert reason == E_RATE_LIMIT

    r_auth = ProcResult(returncode=1, stdout="", stderr="401 Unauthorized login required", timed_out=False, duration_s=1.0)
    code, reason, _ = classify_agent_error(r_auth)
    assert code == EXIT_NEEDS_HUMAN
    assert reason == E_AUTH

    r_ok = ProcResult(returncode=0, stdout='{"ok": true}', stderr="", timed_out=False, duration_s=1.0)
    code, reason, _ = classify_agent_error(r_ok)
    assert code == EXIT_OK
