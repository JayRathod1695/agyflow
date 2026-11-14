"""Patch export block: stages worktree changes, exports binary patch, and computes stats."""

from __future__ import annotations

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
from agyflow.lib.proc import run_command


def main() -> int:
    args = parse_cli_args()
    wt = load_input_payload(args.in_dir, "wt")
    snapshot = load_input_payload(args.in_dir, "snapshot")
    wt_path = Path(wt["path"]).resolve()
    base_commit = str(snapshot["head"])

    approach_id: str | None = None
    app_file = args.in_dir / "approach.json"
    if app_file.is_file():
        try:
            _, app_payload = read_artifact(app_file)
            approach_id = app_payload.get("id")
        except Exception:
            pass

    # Stage changes
    run_command(["git", "add", "-A"], cwd=wt_path)

    # Export full binary diff against base_commit
    diff_res = run_command(["git", "diff", "--cached", "--binary", "--full-index", base_commit], cwd=wt_path)
    patch_bytes = diff_res.stdout.encode("utf-8")

    if not diff_res.stdout.strip():
        write_status(args.out_dir, "failed", E_POLICY, "Actor made no changes (empty patch)", complete=False)
        return EXIT_FAILED

    patch_sha256 = hashlib.sha256(patch_bytes).hexdigest()
    (args.out_dir / "patch.patch").write_bytes(patch_bytes)

    # Name-status: path -> A|M|D|R
    name_res = run_command(["git", "diff", "--cached", "--name-status", base_commit], cwd=wt_path)
    status_map: dict[str, str] = {}
    for line in name_res.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            st = parts[0][0].upper()
            st_code = st if st in ("A", "M", "D", "R") else "M"
            status_map[parts[1].strip()] = st_code

    # Numstat: insertions, deletions, binary
    num_res = run_command(["git", "diff", "--cached", "--numstat", base_commit], cwd=wt_path)
    files_list: list[dict[str, Any]] = []
    binary_files: list[str] = []
    total_ins = 0
    total_del = 0

    for line in num_res.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) == 3:
            ins_str, del_str, p = parts[0], parts[1], parts[2].strip()
            is_bin = (ins_str == "-" or del_str == "-")
            ins = 0 if is_bin else int(ins_str)
            dels = 0 if is_bin else int(del_str)
            if is_bin:
                binary_files.append(p)
            total_ins += ins
            total_del += dels
            files_list.append({
                "path": p,
                "insertions": ins,
                "deletions": dels,
                "status": status_map.get(p, "M"),
            })

    patch_meta = {
        "approach_id": approach_id,
        "base_commit": base_commit,
        "files": files_list,
        "insertions": total_ins,
        "deletions": total_del,
        "patch_sha256": patch_sha256,
        "binary_files": binary_files,
    }

    step = args.ctx.get("step", "patch")
    write_artifact(args.out_dir, "patch_meta", "patch_meta", patch_meta, step=step, block="patch-export")
    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
