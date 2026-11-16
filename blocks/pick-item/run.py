"""Pick item block: selects an element from a JSON array or extracts from a collection directory."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
from typing import Any

from agyflow.lib.contract import (
    E_INPUT,
    EXIT_FAILED,
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    read_artifact,
    write_artifact,
    write_status,
)


def _get_nested(data: Any, path: str) -> Any:
    if not path:
        return data
    curr = data
    for part in path.split("."):
        part = part.strip()
        if not part:
            continue
        if "[" in part and part.endswith("]"):
            name, idx_s = part[:-1].split("[", 1)
            if isinstance(curr, dict) and name:
                curr = curr.get(name)
            if isinstance(curr, list) and idx_s.isdigit() and int(idx_s) < len(curr):
                curr = curr[int(idx_s)]
            else:
                return None
        elif isinstance(curr, dict):
            curr = curr.get(part)
        else:
            return None
    return curr


def main() -> int:
    args = parse_cli_args()
    mode = str(args.cfg.get("mode", "json_list")).strip()
    from_path = str(args.cfg.get("from", "")).strip()
    key_name = str(args.cfg.get("key", "id")).strip()

    target_id: str | None = None
    item_file = args.in_dir / "item.json"
    if item_file.is_file():
        try:
            _, item_payload = read_artifact(item_file)
            if isinstance(item_payload, dict):
                target_id = str(item_payload.get("id", item_payload.get("value", "")))
            else:
                target_id = str(item_payload)
        except Exception:
            pass

    if not target_id:
        selector_file = args.in_dir / "selector.json"
        if selector_file.is_file():
            try:
                _, sel_payload = read_artifact(selector_file)
                val = _get_nested(sel_payload, from_path)
                if val is None and from_path.startswith("payload."):
                    val = _get_nested(sel_payload, from_path[8:])
                if isinstance(val, list) and val:
                    target_id = str(val[0])
                elif val is not None:
                    target_id = str(val)
            except Exception:
                pass

    if not target_id:
        ctx_item = str(args.ctx.get("item_id") or args.ctx.get("item") or args.cfg.get("item") or "")
        if ctx_item:
            target_id = ctx_item
        else:
            for part in str(args.ctx.get("step", "")).split("/"):
                if part.startswith("item."):
                    target_id = part.removeprefix("item.")
                    break

    out_name = "picked"
    out_type = "approach"
    out_decls = args.cfg.get("out") or args.cfg.get("_out_manifest", {})
    if isinstance(out_decls, dict) and out_decls:
        out_name, out_type = next(iter(out_decls.items()))

    if mode == "json_list":
        source_file = args.in_dir / "source.json"
        if not source_file.is_file():
            write_status(args.out_dir, "failed", E_INPUT, "Missing source.json input", complete=False)
            return EXIT_FAILED

        _, source_data = read_artifact(source_file)
        list_container = _get_nested(source_data, from_path) if from_path else source_data
        if not isinstance(list_container, list):
            if isinstance(source_data, dict) and "approaches" in source_data:
                list_container = source_data["approaches"]
            else:
                write_status(args.out_dir, "failed", E_INPUT, f"Path '{from_path}' is not a list", complete=False)
                return EXIT_FAILED

        matched_item: dict[str, Any] | None = None
        for elem in list_container:
            if isinstance(elem, dict) and str(elem.get(key_name, "")) == str(target_id):
                matched_item = elem
                break

        if matched_item is None:
            write_status(args.out_dir, "failed", E_INPUT, f"Item with {key_name}='{target_id}' not found", complete=False)
            return EXIT_FAILED

        write_artifact(args.out_dir, out_name, out_type, matched_item, step=args.ctx.get("step", "pick"), block="pick-item")

    elif mode == "collection":
        collection_dir = args.in_dir / "collection"
        if not collection_dir.is_dir():
            for child in args.in_dir.iterdir():
                if child.is_dir() and not child.name.startswith("_"):
                    collection_dir = child
                    break

        item_dir = collection_dir / str(target_id)
        if not item_dir.is_dir():
            write_status(args.out_dir, "failed", E_INPUT, f"Item directory {target_id} not found in collection", complete=False)
            return EXIT_FAILED

        for src_file in item_dir.iterdir():
            if src_file.is_file():
                dest_file = args.out_dir / src_file.name
                shutil.copy2(src_file, dest_file)
    else:
        write_status(args.out_dir, "failed", E_INPUT, f"Unknown mode: {mode}", complete=False)
        return EXIT_FAILED

    write_status(args.out_dir, status="ok", complete=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
