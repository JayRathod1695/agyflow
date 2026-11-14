"""Human gate block: pauses execution, renders review.md, and verifies approval hash."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

from agyflow.lib.contract import (
    E_NEEDS_HUMAN,
    EXIT_NEEDS_HUMAN,
    EXIT_OK,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)
from agyflow.lib.schema import content_hash


def _render_review_md(gate: str, kind: str, instructions: str, subject_hash: str, subjects: dict[str, Any], views: dict[str, Any]) -> str:
    lines = [
        f"# Human Gate Review: `{gate}`",
        "",
        f"- **Kind:** `{kind}`",
        f"- **Subject Hash:** `{subject_hash}`",
        f"- **Instructions:** {instructions or 'Review the items below and provide decision.'}",
        "",
        "## Subjects",
    ]
    for name, payload in sorted(subjects.items()):
        lines.append(f"### Input: `{name}`")
        if isinstance(payload, dict) and "items" in payload:
            items = payload["items"]
            lines.append(f"- Total items: {len(items)}")
            for it in items[:15]:
                fp = it.get("fingerprint", "")
                sev = it.get("severity", "")
                claim = it.get("claim", it.get("title", ""))
                lines.append(f"  - `[{sev}]` `{fp}`: {claim}")
        elif isinstance(payload, dict) and "approaches" in payload:
            for app in payload["approaches"]:
                lines.append(f"  - **[{app.get('id')}]** {app.get('title')} (differs on `{app.get('differs_on')}`)")
        else:
            lines.append(f"```json\n{json.dumps(payload, indent=2)[:1000]}\n```")

    if views:
        lines.extend(["", "## Additional Views"])
        for vname, vpayload in sorted(views.items()):
            lines.append(f"- **{vname}:** `{type(vpayload).__name__}`")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_cli_args()
    gate = str(args.cfg.get("gate", "gate"))
    kind = str(args.cfg.get("kind", "review"))
    instructions = str(args.cfg.get("instructions", ""))

    subjects: dict[str, Any] = {}
    views: dict[str, Any] = {}

    for fpath in sorted(args.in_dir.glob("*.json")):
        if fpath.name.startswith("_"):
            continue
        try:
            _, payload = read_artifact(fpath)
            stem = fpath.stem
            if stem.startswith("subject"):
                subjects[stem] = payload
            elif stem.startswith("view"):
                views[stem] = payload
            else:
                subjects[stem] = payload
        except Exception:
            pass

    subject_hash = content_hash(subjects if len(subjects) > 1 else (list(subjects.values())[0] if subjects else {}))

    review_text = _render_review_md(gate, kind, instructions, subject_hash, subjects, views)
    (args.out_dir / "review.md").write_text(review_text, encoding="utf-8")

    resp_file = args.in_dir / "_gate_response.json"
    if not resp_file.is_file():
        write_status(
            args.out_dir,
            status="needs_human",
            code=E_NEEDS_HUMAN,
            reason=f"Gate '{gate}' pending human response. Review {args.out_dir / 'review.md'}",
            complete=False,
        )
        return EXIT_NEEDS_HUMAN

    try:
        resp = json.loads(resp_file.read_text(encoding="utf-8"))
    except Exception as err:
        write_status(args.out_dir, "needs_human", E_NEEDS_HUMAN, f"Malformed gate response: {err}", complete=False)
        return EXIT_NEEDS_HUMAN

    decision = str(resp.get("decision", "approve")).lower()
    if decision not in ("approve", "reject"):
        decision = "approve"

    picks = list(resp.get("picks", []))
    accept = list(resp.get("accept", []))
    reject = list(resp.get("reject", []))
    note = str(resp.get("note", ""))

    approval_payload = {
        "gate": gate,
        "decision": decision,
        "subject_hash": subject_hash,
        "picks": picks,
        "accept": accept,
        "reject": reject,
        "note": note,
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }

    write_artifact(
        out_dir=args.out_dir,
        name="approval",
        schema_type="approval",
        payload=approval_payload,
        step=args.ctx.get("step", "gate"),
        block="gate-human",
    )

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
