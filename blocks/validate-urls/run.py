"""URL validator block: protects against SSRF and verifies cited URLs with offline fallback."""

from __future__ import annotations

import ipaddress
from pathlib import Path
import socket
import sys
from typing import Any
import urllib.parse
import urllib.request

from agyflow.lib.contract import (
    EXIT_OK,
    load_input_payload,
    parse_cli_args,
    write_artifact,
    write_status,
)


def _is_ssrf_risk(host: str) -> bool:
    try:
        addrinfo = socket.getaddrinfo(host, None)
        for _, _, _, _, sockaddr in addrinfo:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return True
        return False
    except Exception:
        return True


def _probe_url(url: str, allowed_schemes: list[str], timeout_s: float) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False, "MALFORMED_URL"

    if parsed.scheme.lower() not in allowed_schemes:
        return False, "DISALLOWED_SCHEME"
    if not parsed.hostname:
        return False, "INVALID_HOST"
    if _is_ssrf_risk(parsed.hostname):
        return False, "SSRF_BLOCKED"

    headers = {"User-Agent": "agyflow-validator/1.0"}
    try:
        req = urllib.request.Request(url, method="HEAD", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status in range(200, 400):
                return True, "OK"
    except Exception:
        try:
            req_get = urllib.request.Request(url, method="GET", headers=headers)
            with urllib.request.urlopen(req_get, timeout=timeout_s) as resp:
                if resp.status in range(200, 400):
                    return True, "OK"
        except Exception:
            return False, "URL_DEAD"
    return False, "URL_DEAD"


def main() -> int:
    args = parse_cli_args()
    research = load_input_payload(args.in_dir, "research")

    fetch = bool(args.cfg.get("fetch", True))
    timeout_s = float(args.cfg.get("timeout_s", 10.0))
    allowed_schemes = [s.lower() for s in args.cfg.get("allowed_schemes", ["https"])]
    offline_mode = str(args.cfg.get("offline", "mark")).lower()

    clean_items: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = list(research.get("unresolved", []))
    dropped: list[dict[str, Any]] = []
    total_sources = 0

    all_probes_failed = True

    for item in research.get("items", []):
        uid = str(item.get("unknown_id", ""))
        valid_sources: list[dict[str, Any]] = []

        for src in item.get("sources", []):
            total_sources += 1
            url = str(src.get("url", ""))

            if not fetch:
                src_copy = dict(src)
                src_copy["opened"] = False
                valid_sources.append(src_copy)
                all_probes_failed = False
                continue

            ok, reason = _probe_url(url, allowed_schemes, timeout_s)
            if ok:
                all_probes_failed = False
                src_copy = dict(src)
                src_copy["opened"] = True
                valid_sources.append(src_copy)
            else:
                dropped.append({"id": f"{uid}:{url}", "reason_code": reason, "detail": f"Failed check for {url}"})

        if valid_sources:
            item_copy = dict(item)
            item_copy["sources"] = valid_sources
            clean_items.append(item_copy)
        else:
            if offline_mode == "mark" and all_probes_failed and total_sources > 0:
                item_copy = dict(item)
                item_copy["sources"] = [dict(s, opened=False) for s in item.get("sources", [])]
                clean_items.append(item_copy)
            else:
                unresolved.append({"unknown_id": uid, "reason": "no_valid_sources"})

    warnings: list[str] = []
    if all_probes_failed and total_sources > 0 and offline_mode == "mark":
        warnings.append("urls_unverified")

    clean_payload = {
        "items": clean_items,
        "unresolved": unresolved,
    }

    dropped_payload = {
        "items": dropped,
        "total_in": total_sources,
        "total_dropped": len(dropped),
        "drop_ratio": round(len(dropped) / max(1, total_sources), 4) if total_sources else 0.0,
    }

    step = args.ctx.get("step", "validate_urls")
    write_artifact(args.out_dir, "research_clean", "research", clean_payload, step=step, block="validate-urls")
    write_artifact(args.out_dir, "dropped", "dropped", dropped_payload, step=step, block="validate-urls")

    write_status(args.out_dir, status="ok", complete=True, warnings=warnings)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
