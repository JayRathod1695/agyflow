# agyflow System Context (Gated Source of Truth)

## 1. System Goal
A swappable-block, multi-agent engineering workflow runner built for the agy CLI (Gemini 3.8 Flash).

## 2. Core Constraints
- Python 3.11+ strictly.
- Max lines per file: < 200 lines (advisory target for modularity).
- Third-party dependencies: ONLY `pyyaml` and `jsonschema`.
- Blocks are independent CLI programs (Zero imports between blocks or from engine).
- Communication only via typed JSON/file artifacts on disk.
- Pure Unix contract: `--in <dir> --out <dir> --cfg <json>`.
- Exit codes:
  - 0: ok
  - 10: retryable (timeout, rate-limit, empty/malformed output)
  - 20: failed (contract breach, bad input, policy violation)
  - 30: needs_human (gate paused, ambiguous state)
  - 124: timeout (engine-made)
  - 125: contract violation (engine-made)

## 3. Directory Layout
```text
agyflow/
├── pyproject.toml
├── README.md
├── DECISIONS.md
├── CONTEXT.md
├── ACTIVITY.md
├── src/agyflow/
│   ├── cli.py
│   ├── engine/
│   │   ├── pipeline.py
│   │   ├── expr.py
│   │   ├── store.py
│   │   ├── state.py
│   │   ├── runner.py
│   │   ├── policy.py
│   │   ├── launcher.py
│   │   ├── lock.py
│   │   └── registry.py
│   └── lib/
│       ├── contract.py
│       ├── schema.py
│       ├── proc.py
│       ├── gitutil.py
│       └── agentlib.py
├── blocks/
├── schemas/
├── prompts/
├── profiles/
├── pipelines/
└── tests/
```
