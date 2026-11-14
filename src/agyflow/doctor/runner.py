"""Doctor runner: executes all environment, git, and agy probes."""

import datetime
import json
import sys
from pathlib import Path
from typing import TextIO

from .models import CAPABILITY_PROBE_KEYS, ProbeResult, ProbeStatus
from .probes_agy import (
    probe_agy_capabilities,
    probe_fake_adapter_available,
    probe_live_agy_binary,
)
from .probes_env import probe_dependencies, probe_python_version, probe_readonly_chmod_isolation
from .probes_git import (
    probe_git_author_configured,
    probe_git_installed,
    probe_git_version,
    probe_git_worktree,
)

DEFAULT_CAPABILITIES_PATH = Path.home() / ".agyflow" / "capabilities.json"


class DoctorRunner:
    """Executes environment probes and generates capabilities.json."""

    def __init__(self, live: bool = False, output_path: Path | None = None):
        self.live = live
        self.output_path = output_path or DEFAULT_CAPABILITIES_PATH
        self.results: list[ProbeResult] = []

    def run_all(self) -> list[ProbeResult]:
        self.results = []
        # 1. Environment probes
        self.results.append(probe_python_version())
        self.results.extend(probe_dependencies())
        self.results.append(probe_readonly_chmod_isolation())

        # 2. Git probes
        self.results.append(probe_git_installed())
        self.results.append(probe_git_version())
        self.results.append(probe_git_worktree())
        self.results.append(probe_git_author_configured())

        # 3. Agent probes
        self.results.append(probe_fake_adapter_available())
        self.results.append(probe_live_agy_binary())
        self.results.extend(probe_agy_capabilities(live=self.live))

        return self.results

    def save_capabilities(self) -> Path:
        """Write ~/.agyflow/capabilities.json with the 13 probe keys."""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        cap_map: dict[str, str] = {}
        # Populate the 13 capability probe keys
        for r in self.results:
            if r.key in CAPABILITY_PROBE_KEYS:
                cap_map[r.key] = r.status.value

        # Ensure all 13 keys are explicitly present
        for key in CAPABILITY_PROBE_KEYS:
            if key not in cap_map:
                cap_map[key] = "untested" if not self.live else "unknown"

        payload = {
            "schema_version": "1.0.0",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "live_probes": self.live,
            "capabilities": cap_map,
            "all_probes": [r.to_dict() for r in self.results],
        }

        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return self.output_path

    def print_report(self, out: TextIO = sys.stdout) -> int:
        """Format and print ASCII diagnostic report. Returns exit code 0 or 1."""
        out.write("\n" + "=" * 76 + "\n")
        out.write("                         agyflow doctor diagnosis\n")
        out.write("=" * 76 + "\n")

        status_symbols = {
            ProbeStatus.PASS: "[ PASS ]",
            ProbeStatus.WARN: "[ WARN ]",
            ProbeStatus.FAIL: "[ FAIL ]",
            ProbeStatus.UNTESTED: "[ SKIP ]",
        }

        has_fail = False
        for r in self.results:
            sym = status_symbols.get(r.status, "[ ? ]")
            out.write(f" {sym} {r.name:<40} : {r.message}\n")
            if r.status == ProbeStatus.FAIL:
                has_fail = True

        out.write("-" * 76 + "\n")
        saved_path = self.save_capabilities()
        out.write(f" Capabilities map saved to: {saved_path}\n")
        mode_str = "LIVE" if self.live else "OFFLINE"
        out.write(f" Doctor completed in {mode_str} mode. Status: {'FAIL' if has_fail else 'OK'}\n")
        out.write("=" * 76 + "\n\n")

        return 1 if has_fail else 0


def run_doctor(live: bool = False, output_path: Path | None = None) -> int:
    runner = DoctorRunner(live=live, output_path=output_path)
    runner.run_all()
    return runner.print_report()
