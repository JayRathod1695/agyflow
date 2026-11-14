"""Draft 2020-12 schema validation, canonical JSON serialization, and hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
import jsonschema

_VALIDATOR_CACHE: dict[str, jsonschema.Draft202012Validator] = {}
_SCHEMA_CACHE: dict[str, dict[str, Any]] = {}


class SchemaValidationError(Exception):
    """Raised when data fails schema validation."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or [message]


def canonical_json(data: Any) -> str:
    """Return deterministic canonical JSON string."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(data: Any) -> str:
    """Compute sha256 hex digest of data via canonical JSON."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def hash_file(path: Path | str) -> str:
    """Compute sha256 hex digest of raw file bytes."""
    p = Path(path)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def hash_dir(dir_path: Path | str) -> str:
    """Compute deterministic sha256 hex digest of a directory."""
    p = Path(dir_path)
    entries: list[tuple[str, str]] = []
    for item in sorted(p.rglob("*")):
        if item.is_file():
            rel_path = item.relative_to(p).as_posix()
            entries.append((rel_path, hash_file(item)))
    return content_hash(entries)


def default_schema_dirs() -> list[Path]:
    """Default search directories for schemas."""
    dirs: list[Path] = []
    # 1. Project level .flow/schemas if available
    cwd = Path.cwd()
    dirs.append(cwd / ".flow" / "schemas")
    # 2. User level ~/.agyflow/schemas
    dirs.append(Path.home() / ".agyflow" / "schemas")
    # 3. Packaged schemas/ directory in repo
    pkg_schema_dir = Path(__file__).resolve().parent.parent.parent.parent / "schemas"
    if pkg_schema_dir.exists():
        dirs.append(pkg_schema_dir)
    return dirs


def find_schema_path(schema_name: str, search_dirs: list[Path] | None = None) -> Path:
    """Find schema file path by name (e.g., 'snapshot' or 'snapshot.schema.json')."""
    filename = schema_name if schema_name.endswith(".schema.json") else f"{schema_name}.schema.json"
    dirs = search_dirs or default_schema_dirs()
    for d in dirs:
        candidate = d / filename
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Schema file '{filename}' not found in search paths: {dirs}")


def load_schema(schema_name: str, search_dirs: list[Path] | None = None) -> dict[str, Any]:
    """Load JSON schema from disk, with in-memory caching."""
    if schema_name in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[schema_name]
    path = find_schema_path(schema_name, search_dirs)
    with open(path, "r", encoding="utf-8") as f:
        schema = json.load(f)
    _SCHEMA_CACHE[schema_name] = schema
    return schema


def get_validator(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    """Get or compile a Draft202012Validator for the schema."""
    key = content_hash(schema)
    if key not in _VALIDATOR_CACHE:
        _VALIDATOR_CACHE[key] = jsonschema.Draft202012Validator(schema)
    return _VALIDATOR_CACHE[key]


def validate_data(data: Any, schema: dict[str, Any] | str) -> list[str]:
    """Validate data against schema. Return list of human-friendly error messages."""
    if isinstance(schema, str):
        schema = load_schema(schema)
    validator = get_validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(data), key=lambda e: e.path):
        loc = ".".join(str(p) for p in err.path) if err.path else "root"
        errors.append(f"{loc}: {err.message}")
    return errors


def validate_or_raise(data: Any, schema: dict[str, Any] | str) -> None:
    """Validate data against schema. Raise SchemaValidationError if invalid."""
    errors = validate_data(data, schema)
    if errors:
        name = schema if isinstance(schema, str) else schema.get("title", "schema")
        raise SchemaValidationError(f"Validation failed for {name}: {errors[0]}", errors=errors)
