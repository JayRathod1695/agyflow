"""Resolution order for blocks, prompts, schemas, profiles, and pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class BlockManifest:
    """Parsed manifest for an agyflow block (block.toml)."""

    name: str
    version: str
    command: list[str]
    dir: Path
    side_effects: str = "none"  # none, read_only, temp, worktree, repo, network
    retry_safe: bool = False
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    dynamic_outputs: bool = False


def search_paths(kind: str, repo_root: Path | str | None = None) -> list[Path]:
    """Return resolution paths in order: repo .flow/<kind> -> ~/.agyflow/<kind> -> packaged <kind>."""
    paths: list[Path] = []
    root = Path(repo_root).resolve() if repo_root else Path.cwd()
    paths.append(root / ".flow" / kind)
    paths.append(Path.home() / ".agyflow" / kind)
    # Repo/packaged root
    pkg_dir = Path(__file__).resolve().parent.parent.parent.parent / kind
    if pkg_dir.exists():
        paths.append(pkg_dir)
    return paths


def resolve_file(kind: str, name: str, repo_root: Path | str | None = None, ext: str = "") -> Path:
    """Locate a single configuration file in override order."""
    filename = name if (ext and name.endswith(ext)) else f"{name}{ext}"
    for d in search_paths(kind, repo_root):
        candidate = d / filename
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{kind} file '{filename}' not found in search paths")


def load_manifest(manifest_path: Path) -> BlockManifest:
    """Load and parse block.toml."""
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
    bsec = data.get("block") if isinstance(data.get("block"), dict) else data
    raw_o, raw_i = data.get("outputs", {}), data.get("inputs", {})
    outs = {x["name"]: x for x in raw_o if isinstance(x, dict) and "name" in x} if isinstance(raw_o, list) else (raw_o if isinstance(raw_o, dict) else {})
    ins = {x["name"]: x for x in raw_i if isinstance(x, dict) and "name" in x} if isinstance(raw_i, list) else (raw_i if isinstance(raw_i, dict) else {})
    return BlockManifest(
        name=bsec.get("name", manifest_path.parent.name),
        version=str(bsec.get("version", "1")),
        command=bsec.get("command", ["python3.11", "main.py"]),
        dir=manifest_path.parent,
        side_effects=bsec.get("side_effects", "none"),
        retry_safe=bool(bsec.get("retry_safe", False)),
        inputs=ins,
        outputs=outs,
        dynamic_outputs=bool(bsec.get("dynamic_outputs", False)),
    )


def resolve_block(
    block_spec: str,
    project_cfg: dict[str, Any] | None = None,
    repo_root: Path | str | None = None,
) -> BlockManifest:
    """Resolve block specifier (alias -> name@version -> directory) and load manifest."""
    # 1. Alias substitution from project.toml [blocks]
    target = block_spec
    if project_cfg and "blocks" in project_cfg and block_spec in project_cfg["blocks"]:
        target = project_cfg["blocks"][block_spec]

    req_name, _, req_ver = target.partition("@")
    manifests: list[BlockManifest] = []

    for base_dir in search_paths("blocks", repo_root):
        if not base_dir.is_dir():
            continue
        # Direct folder match or scan folders
        candidates = [base_dir / req_name] if (base_dir / req_name).is_dir() else list(base_dir.iterdir())
        for d in candidates:
            mpath = d / "block.toml"
            if mpath.is_file():
                try:
                    mf = load_manifest(mpath)
                    if mf.name == req_name:
                        if not req_ver or mf.version == req_ver:
                            return mf
                        manifests.append(mf)
                except Exception:
                    continue

    if manifests:
        return manifests[0]
    raise FileNotFoundError(f"Block '{block_spec}' (resolved to '{target}') not found")


def resolve_profile(name: str, repo_root: Path | str | None = None) -> dict[str, Any]:
    """Resolve and parse profile TOML file."""
    path = resolve_file("profiles", name, repo_root, ext=".toml")
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_pipeline_path(name: str, repo_root: Path | str | None = None) -> Path:
    """Resolve path to a pipeline YAML file."""
    ext = ".yaml" if not (name.endswith(".yaml") or name.endswith(".yml")) else ""
    return resolve_file("pipelines", name, repo_root, ext=ext)
