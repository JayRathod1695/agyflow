"""Project publish block: updates .flow/CONTEXT.json and appends approved decisions."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

from agyflow.lib.contract import (
    EXIT_OK,
    atomic_write_json,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)
from agyflow.lib.schema import canonical_json


def main() -> int:
    args = parse_cli_args()
    flow_dir_raw = args.ctx.get("flow_dir")
    if flow_dir_raw:
        flow_dir = Path(flow_dir_raw).resolve()
    else:
        repo_path = Path(args.ctx.get("repo", ".")).resolve()
        flow_dir = repo_path / ".flow"

    flow_dir.mkdir(parents=True, exist_ok=True)

    # 1. Update .flow/CONTEXT.json if context input is present and changed
    context_file = args.in_dir / "context.json"
    context_written = False
    if context_file.is_file():
        context_payload = load_input_payload(args.in_dir, "context")
        target_context = flow_dir / "CONTEXT.json"
        should_write = True
        if target_context.is_file():
            try:
                existing_text = target_context.read_text(encoding="utf-8")
                if existing_text.strip() == canonical_json(context_payload):
                    should_write = False
            except Exception:
                should_write = True

        if should_write:
            atomic_write_json(target_context, context_payload)
            context_written = True

    # 2. Append decisions from approval* inputs to .flow/decisions.md
    decisions_file = flow_dir / "decisions.md"
    existing_lines: set[str] = set()
    if decisions_file.is_file():
        existing_lines = {line.strip() for line in decisions_file.read_text(encoding="utf-8").splitlines()}

    new_entries: list[str] = []
    run_id = args.ctx.get("run_id", "run")
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for approval_path in sorted(args.in_dir.glob("approval*.json")):
        try:
            _, payload = read_artifact(approval_path)
            note = str(payload.get("note", "")).strip()
            if note:
                gate_name = payload.get("gate", "gate")
                entry = f"- [{date_str} {gate_name} {run_id}] {note}"
                if entry not in existing_lines and entry not in new_entries:
                    new_entries.append(entry)
        except Exception:
            continue

    if new_entries:
        with open(decisions_file, "a", encoding="utf-8") as f:
            for entry in new_entries:
                f.write(f"{entry}\n")

    publish_payload = {
        "context_written": context_written,
        "decisions_appended": len(new_entries),
    }

    write_artifact(
        out_dir=args.out_dir,
        name="publish_result",
        schema_type="publish_result",
        payload=publish_payload,
        step=args.ctx.get("step", "publish"),
        block="project-publish",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
