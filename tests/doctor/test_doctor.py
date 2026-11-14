"""Unit tests for agyflow doctor runner and capability mapping."""

import json
from pathlib import Path
from agyflow.doctor import CAPABILITY_PROBE_KEYS, DoctorRunner, ProbeStatus
from agyflow.doctor.probes_env import probe_dependencies, probe_python_version
from agyflow.doctor.probes_git import probe_git_installed, probe_git_version


def test_doctor_offline_capabilities_map(tmp_path: Path):
    """Verify offline doctor produces capabilities.json with all 13 keys marked 'untested'."""
    cap_path = tmp_path / "capabilities.json"
    runner = DoctorRunner(live=False, output_path=cap_path)
    runner.run_all()
    saved = runner.save_capabilities()

    assert saved.exists()
    with open(saved, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["schema_version"] == "1.0.0"
    assert data["live_probes"] is False
    assert "capabilities" in data

    caps = data["capabilities"]
    assert len(caps) == 13
    for key in CAPABILITY_PROBE_KEYS:
        assert key in caps
        assert caps[key] == "untested"


def test_doctor_python_probe():
    """Verify python probe passes under Python 3.11+."""
    res = probe_python_version()
    assert res.status == ProbeStatus.PASS
    assert "3.11" in res.message or "3.12" in res.message or "3.13" in res.message


def test_doctor_git_probe():
    """Verify git executable probe detects git."""
    res = probe_git_installed()
    assert res.status in (ProbeStatus.PASS, ProbeStatus.WARN)


def test_doctor_dependencies_probe():
    """Verify pyyaml and jsonschema probes execute."""
    results = probe_dependencies()
    assert len(results) == 2
    keys = {r.key for r in results}
    assert "dep_pyyaml" in keys
    assert "dep_jsonschema" in keys


def test_doctor_runner_execution_and_report(tmp_path: Path):
    """Verify complete DoctorRunner cycle runs without throwing and writes report."""
    import io

    cap_path = tmp_path / "doctor_test.json"
    runner = DoctorRunner(live=False, output_path=cap_path)
    runner.run_all()

    buf = io.StringIO()
    exit_code = runner.print_report(out=buf)
    output = buf.getvalue()

    assert "agyflow doctor diagnosis" in output
    assert "Capabilities map saved to" in output
    assert exit_code in (0, 1)
    assert cap_path.exists()
