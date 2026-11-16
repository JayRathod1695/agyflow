"""Pipeline YAML parsing, include expansion, ref syntax checks, and variable substitution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
import yaml

from agyflow.engine.registry import resolve_pipeline_path

VAR_SUB_RE = re.compile(r"\$\{([a-zA-Z0-9_\.]+)\}")


@dataclass
class PipelineNode:
    """A single node in a pipeline: step, loop, foreach, or include."""

    kind: str  # "step", "loop", "foreach", "include"
    id: str
    data: dict[str, Any]
    steps: list[PipelineNode] = field(default_factory=list)


@dataclass
class PipelineTree:
    """Parsed, fully-expanded pipeline ready for DAG execution."""

    name: str
    version: int
    inputs: list[str]
    params: dict[str, Any]
    steps: list[PipelineNode]
    exports: dict[str, str]
    finally_: list[PipelineNode]


def substitute_variables(val: Any, scope_lookup: dict[str, Any]) -> Any:
    """Substitute ${...} variables. Exact single match preserves type; embedded string interpolates."""
    if isinstance(val, str):
        val_str = val.strip()
        exact = re.fullmatch(r"\$\{([a-zA-Z0-9_\.]+)\}", val_str)
        if exact:
            return scope_lookup.get(exact.group(1), val)
        return VAR_SUB_RE.sub(lambda m: str(scope_lookup.get(m.group(1), "")), val)
    if isinstance(val, dict):
        return {k: substitute_variables(v, scope_lookup) for k, v in val.items()}
    if isinstance(val, list):
        return [substitute_variables(x, scope_lookup) for x in val]
    return val


def _rewrite_refs(data: Any, prefix: str, declared_inputs: dict[str, str]) -> Any:
    """Rewrite step references inside included subtrees with prefix, mapping with-inputs."""
    if isinstance(data, str):
        s = re.sub(r"\$(?!(?:project|prev|loop|item)\.)([a-zA-Z0-9_]+)", rf"${prefix}_\1", data)
        for inp_name, mapped_ref in declared_inputs.items():
            s = s.replace(f"${prefix}_{inp_name}", mapped_ref).replace(f"${inp_name}", mapped_ref)
        return s
    if isinstance(data, dict):
        return {k: _rewrite_refs(v, prefix, declared_inputs) for k, v in data.items()}
    if isinstance(data, list):
        return [_rewrite_refs(x, prefix, declared_inputs) for x in data]
    return data


def _expand_nodes(
    raw_nodes: list[dict[str, Any]],
    repo_root: Path | str | None,
    collected_exports: dict[str, str],
    depth: int = 1,
    seen_includes: set[str] | None = None,
) -> list[PipelineNode]:
    """Recursively expand include nodes and parse loops/steps up to depth 3."""
    if depth > 3:
        raise ValueError("Pipeline nesting exceeded maximum depth of 3")
    seen = seen_includes or set()
    result: list[PipelineNode] = []

    for raw in raw_nodes:
        if "include" in raw:
            inc_name = raw["include"]
            alias = raw.get("as", inc_name)
            if inc_name in seen:
                raise ValueError(f"Cycle detected in pipeline include: {inc_name}")

            inc_path = resolve_pipeline_path(inc_name, repo_root)
            with open(inc_path, "r", encoding="utf-8") as f:
                inc_yaml = yaml.safe_load(f) or {}

            with_map = raw.get("with", {})
            for exp_name, exp_ref in inc_yaml.get("exports", {}).items():
                collected_exports[f"{alias}.{exp_name}"] = _rewrite_refs(exp_ref, alias, with_map)

            inc_params = {k: v for k, v in inc_yaml.get("params", {}).items()} | {f"params.{k}": v for k, v in inc_yaml.get("params", {}).items()}
            raw_inc_steps = substitute_variables(inc_yaml.get("steps", []), inc_params)
            rewritten_steps = _rewrite_refs(raw_inc_steps, alias, with_map)
            expanded_inner = _expand_nodes(rewritten_steps, repo_root, collected_exports, depth, seen | {inc_name})
            for n in expanded_inner:
                n.id = f"{alias}_{n.id}"
                result.append(n)

        elif "loop" in raw:
            sub = _expand_nodes(raw.get("steps", []), repo_root, collected_exports, depth + 1, seen)
            result.append(PipelineNode(kind="loop", id=raw["loop"], data=raw, steps=sub))

        elif "foreach" in raw:
            sub = _expand_nodes(raw.get("steps", []), repo_root, collected_exports, depth + 1, seen)
            result.append(PipelineNode(kind="foreach", id=raw["foreach"], data=raw, steps=sub))

        elif "id" in raw:
            result.append(PipelineNode(kind="step", id=raw["id"], data=raw))
        else:
            raise ValueError(f"Unrecognized node in steps list: {raw}")

    return result


def load_pipeline(
    pipeline_file: Path | str,
    project_cfg: dict[str, Any] | None = None,
    set_params: dict[str, Any] | None = None,
    repo_root: Path | str | None = None,
) -> PipelineTree:
    """Load, validate grammar, and expand includes for a pipeline YAML."""
    p_path = Path(pipeline_file)
    if not p_path.is_file():
        p_path = resolve_pipeline_path(str(pipeline_file), repo_root)

    with open(p_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    params = data.get("params", {})
    if project_cfg and "params" in project_cfg:
        params.update(project_cfg["params"])
    if set_params:
        params.update(set_params)

    exports = dict(data.get("exports", {}))
    steps = _expand_nodes(data.get("steps", []), repo_root, exports)
    finally_nodes = _expand_nodes(data.get("finally", []), repo_root, exports)

    return PipelineTree(
        name=data.get("name", p_path.stem),
        version=int(data.get("version", 1)),
        inputs=data.get("inputs", []),
        params=params,
        steps=steps,
        exports=exports,
        finally_=finally_nodes,
    )


def save_expanded_pipeline(tree: PipelineTree, out_file: Path | str) -> None:
    """Serialize expanded pipeline to YAML file for provenance and debugging."""
    p = Path(out_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = {
        "name": tree.name, "version": tree.version, "inputs": tree.inputs,
        "params": tree.params, "exports": tree.exports,
        "steps": [n.data for n in tree.steps], "finally": [n.data for n in tree.finally_],
    }
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False)
