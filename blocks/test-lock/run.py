"""Test lock block: extracts committed tests from HEAD, computes manifest, and marks read-only."""

from __future__ import annotations

import io
from pathlib import Path
import subprocess
import sys
import tarfile

from agyflow.lib.contract import (
    E_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)
from agyflow.lib.gitutil import make_readonly
from agyflow.lib.proc import run_command
from agyflow.lib.schema import content_hash, hash_file


def main() -> int:
    args = parse_cli_args()
    snapshot = load_input_payload(args.in_dir, "snapshot")
    head = snapshot["head"]
    repo_path = Path(args.ctx.get("repo", ".")).resolve()

    raw_paths = args.cfg.get("paths", ["tests"])
    paths = [raw_paths] if isinstance(raw_paths, str) else list(raw_paths)

    existing_paths: list[str] = []
    missing_paths: list[str] = []

    for p in paths:
        res = run_command(["git", "ls-tree", head, p], cwd=repo_path)
        if res.returncode == 0 and res.stdout.strip():
            existing_paths.append(p)
        else:
            missing_paths.append(p)

    if not existing_paths:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"All test paths missing at commit {head[:7]}: {paths}",
            complete=False,
        )
        return EXIT_FAILED

    out_locked = args.out_dir / "locked_tests"
    out_locked.mkdir(parents=True, exist_ok=True)

    archive_cmd = ["git", "archive", head, "--"] + existing_paths
    proc = subprocess.Popen(
        archive_cmd,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    archive_bytes, err_bytes = proc.communicate()

    if proc.returncode != 0:
        err_msg = err_bytes.decode("utf-8", errors="replace").strip()
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"git archive failed: {err_msg}",
            complete=False,
        )
        return EXIT_FAILED

    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as tar:
            tar.extractall(path=out_locked)
    except Exception as err:
        write_status(
            args.out_dir,
            status="failed",
            code=E_INPUT,
            reason=f"Failed to unpack test archive: {err}",
            complete=False,
        )
        return EXIT_FAILED

    files_map: dict[str, str] = {}
    for item in sorted(out_locked.rglob("*")):
        if item.is_file():
            rel_name = item.relative_to(out_locked).as_posix()
            files_map[rel_name] = hash_file(item)

    entries = [(k, files_map[k]) for k in sorted(files_map.keys())]
    root_hash = content_hash(entries)

    make_readonly(out_locked)

    warnings: list[str] = []
    if missing_paths:
        warnings.append(f"missing_test_paths: {', '.join(missing_paths)}")

    if snapshot.get("dirty"):
        st_res = run_command(["git", "status", "--porcelain=v1"], cwd=repo_path)
        dirty_files = [line[3:].strip() for line in st_res.stdout.splitlines() if line.strip()]
        dirty_test = [
            df for df in dirty_files
            if any(df == p or df.startswith(p.rstrip("/") + "/") for p in paths)
        ]
        if dirty_test:
            warnings.append("dirty_tests_ignored")

    manifest_payload = {
        "paths": paths,
        "files": files_map,
        "root_hash": root_hash,
    }

    write_artifact(
        out_dir=args.out_dir,
        name="test_manifest",
        schema_type="test_manifest",
        payload=manifest_payload,
        step=args.ctx.get("step", "tests"),
        block="test-lock",
    )

    write_status(args.out_dir, status="ok", complete=True, warnings=warnings)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
