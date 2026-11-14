"""Environment and dependency probes for agyflow doctor."""

import os
import stat
import sys
import tempfile
from pathlib import Path
from .models import ProbeResult, ProbeStatus


def probe_python_version() -> ProbeResult:
    """Verify runtime Python is at least 3.11."""
    major, minor, micro = sys.version_info.major, sys.version_info.minor, sys.version_info.micro
    v_str = f"{major}.{minor}.{micro}"
    if (major, minor) >= (3, 11):
        return ProbeResult(
            key="python_version",
            name="Python 3.11+ Runtime",
            status=ProbeStatus.PASS,
            message=f"Running Python {v_str}",
            details={"version": v_str},
        )
    return ProbeResult(
        key="python_version",
        name="Python 3.11+ Runtime",
        status=ProbeStatus.FAIL,
        message=f"Python >= 3.11 required; currently {v_str}",
        details={"version": v_str},
    )


def probe_dependencies() -> list[ProbeResult]:
    """Verify approved 3rd-party dependencies (pyyaml, jsonschema) are importable."""
    import importlib.metadata

    results = []
    deps = [("yaml", "pyyaml"), ("jsonschema", "jsonschema")]
    for mod_name, pkg_name in deps:
        try:
            __import__(mod_name)
            try:
                v = importlib.metadata.version(pkg_name)
            except Exception:
                v = "installed"
            results.append(
                ProbeResult(
                    key=f"dep_{pkg_name}",
                    name=f"Dependency: {pkg_name}",
                    status=ProbeStatus.PASS,
                    message=f"Available ({v})",
                    details={"version": str(v)},
                )
            )
        except ImportError:
            results.append(
                ProbeResult(
                    key=f"dep_{pkg_name}",
                    name=f"Dependency: {pkg_name}",
                    status=ProbeStatus.FAIL,
                    message=f"Missing required dependency '{pkg_name}'",
                )
            )
    return results


def probe_readonly_chmod_isolation() -> ProbeResult:
    """Verify filesystem supports chmod a-w write-block isolation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_dir = Path(tmp_dir) / "locked"
        test_dir.mkdir()
        try:
            # Remove write permissions
            test_dir.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
            # Attempt to create file inside locked directory
            blocked = False
            try:
                (test_dir / "forbidden.txt").write_text("violation")
            except (PermissionError, OSError):
                blocked = True
            finally:
                # Restore permissions for cleanup
                test_dir.chmod(stat.S_IRWXU)

            if blocked:
                return ProbeResult(
                    key="chmod_isolation",
                    name="Read-Only chmod Isolation",
                    status=ProbeStatus.PASS,
                    message="Filesystem correctly enforces chmod a-w write blocking",
                )
            return ProbeResult(
                key="chmod_isolation",
                name="Read-Only chmod Isolation",
                status=ProbeStatus.WARN,
                message="Filesystem did not block write after chmod a-w (root/superuser?)",
            )
        except Exception as exc:
            return ProbeResult(
                key="chmod_isolation",
                name="Read-Only chmod Isolation",
                status=ProbeStatus.FAIL,
                message=f"chmod isolation test error: {exc}",
            )
