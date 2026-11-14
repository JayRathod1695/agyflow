"""Acceptance tests for M3 planning validators: token Jaccard (AT3.5) and SSRF drop (AT3.7)."""

import ipaddress
import re
from urllib.parse import urlparse
import pytest


def tokenize(text: str) -> set[str]:
    """Extract lowercased alphanumeric tokens."""
    return set(re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower()))


def token_jaccard(text_a: str, text_b: str) -> float:
    """Compute Jaccard token similarity between two text descriptions."""
    set_a = tokenize(text_a)
    set_b = tokenize(text_b)
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def validate_distinct_plans(plans: list[dict], threshold: float = 0.70) -> dict:
    """AT3.5: Validates candidate plans and flags/drops plans with pairwise Jaccard > 0.70."""
    distinct_plans = []
    dropped_duplicates = []

    for plan in plans:
        desc = plan.get("description", "") + " " + " ".join(plan.get("steps", []))
        is_dup = False
        for kept in distinct_plans:
            kept_desc = kept.get("description", "") + " " + " ".join(kept.get("steps", []))
            sim = token_jaccard(desc, kept_desc)
            if sim > threshold:
                is_dup = True
                dropped_duplicates.append({"id": plan.get("id"), "similar_to": kept.get("id"), "similarity": sim})
                break
        if not is_dup:
            distinct_plans.append(plan)

    return {
        "valid": len(dropped_duplicates) == 0,
        "distinct_plans": distinct_plans,
        "dropped_duplicates": dropped_duplicates,
    }


def is_ssrf_safe_url(url_str: str) -> bool:
    """AT3.7: Checks if a URL is safe from SSRF attacks."""
    try:
        parsed = urlparse(url_str)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False

        if hostname.lower() in ("localhost", "127.0.0.1", "::1", "metadata.google.internal"):
            return False

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or str(ip) == "169.254.169.254":
                return False
        except ValueError:
            # Domain name, check for forbidden patterns
            if hostname.endswith(".local") or hostname.endswith(".internal"):
                return False

        return True
    except Exception:
        return False


def filter_safe_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    """AT3.7: Partitions URLs into (safe_urls, dropped_ssrf_urls)."""
    safe: list[str] = []
    dropped: list[str] = []
    for u in urls:
        if is_ssrf_safe_url(u):
            safe.append(u)
        else:
            dropped.append(u)
    return safe, dropped


def test_validate_distinct_accepts_diverse_plans_at35():
    """AT3.5: Distinct plans with low pairwise Jaccard pass validation."""
    plans = [
        {"id": "P1", "description": "Refactor calculator discount applying before tax compounding", "steps": ["edit calc.py", "run pytest"]},
        {"id": "P2", "description": "Introduce strategy pattern for tax rules and regional tariffs", "steps": ["create strategy.py", "integrate context"]},
    ]
    res = validate_distinct_plans(plans, threshold=0.70)
    assert res["valid"] is True
    assert len(res["distinct_plans"]) == 2
    assert len(res["dropped_duplicates"]) == 0


def test_validate_distinct_drops_collapsed_duplicates_at35():
    """AT3.5: Pairwise Jaccard > 0.70 flags duplicate variation as collapsed."""
    plan_a = {"id": "P1", "description": "Apply discount before sales tax in calculator core", "steps": ["fix discount logic", "verify pytest"]}
    # Plan B is nearly identical wording
    plan_b = {"id": "P2", "description": "Apply discount before sales tax in calculator core module", "steps": ["fix discount logic", "verify pytest suite"]}

    res = validate_distinct_plans([plan_a, plan_b], threshold=0.70)
    assert res["valid"] is False
    assert len(res["distinct_plans"]) == 1
    assert len(res["dropped_duplicates"]) == 1
    assert res["dropped_duplicates"][0]["id"] == "P2"
    assert res["dropped_duplicates"][0]["similar_to"] == "P1"
    assert res["dropped_duplicates"][0]["similarity"] > 0.70


def test_validate_urls_blocks_ssrf_and_private_ips_at37():
    """AT3.7: Private IPs, loopbacks, cloud metadata, and non-HTTP URLs are dropped."""
    unsafe_urls = [
        "http://127.0.0.1/admin",
        "http://localhost:8080/metrics",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.1.5:9000/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.22/secrets",
        "file:///etc/passwd",
        "ftp://ftp.internal.local/data",
        "gopher://evil.internal",
    ]
    safe_urls = [
        "https://docs.python.org/3/library/argparse.html",
        "https://github.com/astral-sh/ruff",
        "https://pypi.org/project/jsonschema/",
    ]

    all_urls = unsafe_urls + safe_urls
    passed, dropped = filter_safe_urls(all_urls)

    assert set(passed) == set(safe_urls)
    assert set(dropped) == set(unsafe_urls)
