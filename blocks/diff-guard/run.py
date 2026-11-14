"""Diff guard block: enforces tamper, test, dependency, file count, and path constraints on patches."""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    E_POLICY,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)


def _matches(path_str: str, globs: list[str]) -> bool:
    p = path_str.strip().lstrip("/")
    for g in globs:
        clean_g = g.strip().lstrip("/")
        if fnmatch.fnmatch(p, clean_g) or fnmatch.fnmatch(Path(p).name, clean_g):
            return True
        if p.startswith(clean_g.rstrip("/*") + "/"):
            return True
    return False


def main() -> int:
    args = parse_cli_args()
    patch_meta = load_input_payload(args.in_dir, "patch_meta")
    test_manifest = load_input_payload(args.in_dir, "test_manifest")

    patch_file = args.in_dir / "patch.patch"
    if not patch_file.is_file():
        # Check any file ending in .patch
        for cand in args.in_dir.glob("*.patch"):
            patch_file = cand
            break

    patch_bytes = patch_file.read_bytes() if patch_file.is_file() else b""
    actual_sha = hashlib.sha256(patch_bytes).hexdigest()

    violations: list[dict[str, str]] = []

    # 1. PATCH_TAMPERED
    expected_sha = patch_meta.get("patch_sha256", "")
    if actual_sha != expected_sha:
        violations.append({"code": "PATCH_TAMPERED", "detail": f"Patch sha {actual_sha} != {expected_sha}"})

    files = patch_meta.get("files", [])
    file_paths = [f["path"] for f in files if "path" in f]

    # 2. BAD_PATH
    for fp in file_paths:
        p = Path(fp)
        if p.is_absolute() or ".." in p.parts:
            violations.append({"code": "BAD_PATH", "detail": f"Path '{fp}' is absolute or traverses parents"})

    # 3. FORBIDDEN_PATH
    forbidden = list(args.cfg.get("forbidden", [".flow", "tests", "data", "*.ckpt", "*.pt"]))
    for fp in file_paths:
        if _matches(fp, forbidden):
            violations.append({"code": "FORBIDDEN_PATH", "detail": f"File '{fp}' hits forbidden glob"})

    # 4. TEST_TAMPER
    test_paths = list(test_manifest.get("paths", []))
    manifest_files = set(test_manifest.get("files", {}).keys())
    for fp in file_paths:
        if _matches(fp, test_paths) or fp in manifest_files:
            violations.append({"code": "TEST_TAMPER", "detail": f"File '{fp}' modifies locked test suite"})

    # 5. DEP_CHANGE
    allow_deps = bool(args.cfg.get("allow_new_deps", False))
    if not allow_deps:
        dep_globs = list(args.cfg.get("dep_files", ["requirements.txt", "pyproject.toml", "setup.py", "package.json"]))
        for fp in file_paths:
            if _matches(fp, dep_globs):
                violations.append({"code": "DEP_CHANGE", "detail": f"Dependency file '{fp}' modified without permission"})

    # 6. TOO_MANY_FILES
    max_f = int(args.cfg.get("max_files", 20))
    app_file = args.in_dir / "approach.json"
    if app_file.is_file():
        try:
            _, app = read_artifact(app_file)
            app_max = int(app.get("max_files_changed", max_f))
            max_f = min(max_f, app_max)
        except Exception:
            pass

    if len(file_paths) > max_f:
        violations.append({"code": "TOO_MANY_FILES", "detail": f"Changed {len(file_paths)} files; ceiling is {max_f}"})

    # 7. TOO_BIG
    total_lines = int(patch_meta.get("insertions", 0)) + int(patch_meta.get("deletions", 0))
    max_lines = int(args.cfg.get("max_changed_lines", 1000))
    if total_lines > max_lines:
        violations.append({"code": "TOO_BIG", "detail": f"Changed {total_lines} lines; limit is {max_lines}"})

    # 8. BINARY
    allow_bin = bool(args.cfg.get("allow_binary", False))
    binary_files = list(patch_meta.get("binary_files", []))
    if not allow_bin and binary_files:
        violations.append({"code": "BINARY", "detail": f"Binary changes detected in: {', '.join(binary_files)}"})

    # 9. SYMLINK
    patch_text = patch_bytes.decode("utf-8", errors="replace")
    if "new file mode 120000" in patch_text or "old mode 120000" in patch_text:
        violations.append({"code": "SYMLINK", "detail": "Patch creates or modifies symbolic link (mode 120000)"})

    passed = (len(violations) == 0)
    guard_payload = {
        "ok": passed,
        "violations": violations,
    }

    step = args.ctx.get("step", "guard")
    write_artifact(args.out_dir, "guard", "guard", guard_payload, step=step, block="diff-guard")

    if not passed:
        reasons = "; ".join(f"{v['code']}: {v['detail']}" for v in violations[:3])
        write_status(args.out_dir, "failed", E_POLICY, f"Guard rejected patch: {reasons}", complete=False)
        return EXIT_FAILED

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
