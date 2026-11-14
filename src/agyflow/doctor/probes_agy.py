"""agy CLI capability probes and 13-key capability map generation."""

import shutil
import subprocess
import sys
from pathlib import Path
from .models import CAPABILITY_PROBE_KEYS, ProbeResult, ProbeStatus

FAKE_AGY_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "tests"
    / "fixtures"
    / "scripts"
    / "fake_agy.py"
)


def probe_fake_adapter_available() -> ProbeResult:
    """Verify offline mock agent adapter (fake_agy.py) is present and functional."""
    if not FAKE_AGY_PATH.exists():
        return ProbeResult(
            key="fake_agent_adapter",
            name="Fake Agent Adapter (fake_agy.py)",
            status=ProbeStatus.WARN,
            message="fake_agy.py not found; offline testing limited",
        )
    try:
        proc = subprocess.run(
            [sys.executable, str(FAKE_AGY_PATH), "--print", "ping"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=True,
        )
        if "schema_version" in proc.stdout or "status" in proc.stdout:
            return ProbeResult(
                key="fake_agent_adapter",
                name="Fake Agent Adapter (fake_agy.py)",
                status=ProbeStatus.PASS,
                message="fake_agy.py operational for offline pipeline runs",
                details={"path": str(FAKE_AGY_PATH)},
            )
        return ProbeResult(
            key="fake_agent_adapter",
            name="Fake Agent Adapter (fake_agy.py)",
            status=ProbeStatus.WARN,
            message="fake_agy.py returned unexpected payload",
        )
    except Exception as exc:
        return ProbeResult(
            key="fake_agent_adapter",
            name="Fake Agent Adapter (fake_agy.py)",
            status=ProbeStatus.WARN,
            message=f"fake_agy.py check failed: {exc}",
        )


def probe_live_agy_binary() -> ProbeResult:
    """Check whether real agy CLI executable exists in system PATH."""
    agy_bin = shutil.which("agy")
    if not agy_bin:
        return ProbeResult(
            key="agy_binary_installed",
            name="agy CLI Binary Installed",
            status=ProbeStatus.WARN,
            message="agy binary not found in PATH (use fake_agy for offline runs)",
        )
    return ProbeResult(
        key="agy_binary_installed",
        name="agy CLI Binary Installed",
        status=ProbeStatus.PASS,
        message=f"agy binary found at {agy_bin}",
        details={"path": agy_bin},
    )


def probe_agy_capabilities(live: bool = False) -> list[ProbeResult]:
    """Evaluate the 13 agy CLI capability probes (Section 9.6).

    Args:
        live: If False, marks capabilities as 'untested' without invoking live LLM CLI.
    """
    results: list[ProbeResult] = []

    if not live:
        for key in CAPABILITY_PROBE_KEYS:
            results.append(
                ProbeResult(
                    key=key,
                    name=f"Capability: {key}",
                    status=ProbeStatus.UNTESTED,
                    message="Untested in offline mode (run with --live to probe agy CLI)",
                )
            )
        return results

    agy_bin = shutil.which("agy")
    if not agy_bin:
        for key in CAPABILITY_PROBE_KEYS:
            results.append(
                ProbeResult(
                    key=key,
                    name=f"Capability: {key}",
                    status=ProbeStatus.FAIL,
                    message="Cannot evaluate live capability: agy binary not found in PATH",
                )
            )
        return results

    # Live probing: check agy help for supported flags and capabilities
    try:
        proc = subprocess.run([agy_bin, "--help"], capture_output=True, text=True, timeout=5.0)
        help_text = (proc.stdout + proc.stderr).lower()
    except Exception as exc:
        help_text = ""

    for key in CAPABILITY_PROBE_KEYS:
        # Check flag presence in help text or default to untested/pass based on probed evidence
        flag_match = False
        if key == "print_json_envelope" and "--print" in help_text:
            flag_match = True
        elif key == "json_schema_enforced" and "--schema" in help_text:
            flag_match = True
        elif key == "stream_json_tool_events" and "stream" in help_text:
            flag_match = True

        status = ProbeStatus.PASS if flag_match else ProbeStatus.WARN
        msg = f"Live probed against {agy_bin}" if flag_match else "Live flag not found in help output"
        results.append(
            ProbeResult(
                key=key,
                name=f"Capability: {key}",
                status=status,
                message=msg,
            )
        )

    return results
