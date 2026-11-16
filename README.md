# agyflow

Autonomous software engineering engine and deterministic agent workflow orchestrator built for the Antigravity CLI and Unix-style pipeline execution.

```
+-----------------------------------------------------------------------------------------+
|                                  AGYFLOW ARCHITECTURE                                   |
+-----------------------------------------------------------------------------------------+
|                                                                                         |
|  [Preflight]   Lock tests, snapshot HEAD, build context                 ==> [PASS]      |
|  [Analyze]     Analyst agent, validate quotes, run reproducers          ==> [PASS]      |
|  [Gate 1]      Findings Gate (Accept/Reject defect claims)              ==> [HUMAN]     |
|  [Plan]        Planner agent, diversity check, research unknowns       ==> [PASS]      |
|  [Gate 2]      Plan Gate (Select winning approach)                      ==> [HUMAN]     |
|  [Act Foreach] Isolated Git worktrees ('act-<id>'), edits, diff guard   ==> [PASS]      |
|  [Judge & Rank]Multi-seed test runner (flaky defense), score & rank     ==> [PASS]      |
|  [Gate 3]      Candidate Gate (Approve final candidate patch)           ==> [HUMAN]     |
|  [Merge & Pub] Create branch 'agyflow/<run>', apply patch, write docs   ==> [PASS]      |
|  [Finally]     Generate REPORT.md (COMPLETED), prune scratch worktrees  ==> [CLEAN]     |
|                                                                                         |
+-----------------------------------------------------------------------------------------+
```

---

## Key Features

- **Unix Contract Architecture:** Every task is a standalone CLI block adhering to Section 4 Unix standards: `--in <dir> --out <dir> --cfg <json>`.
- **Zero-Pollution Git Worktrees:** Actor edits and test evaluations execute inside isolated throwaway worktrees. Main repo `HEAD` stays untouched.
- **Zero-Trust Guardrails:**
  - **Diff Guard:** Detects test tampering, forbidden files, dependency additions, or patch size blowups.
  - **Evidence Validator:** Verifies analyst claims against exact line-by-line file quotes to eliminate hallucination.
- **Multi-Seed Candidate Judging:** Evaluates patches across pseudo-random seeds (`11, 22, 33...`) to catch flaky tests before ranking.
- **Human-in-the-Loop Gates:** Pauses pipeline execution (`paused_gate`) with Markdown reviews; resumes upon receiving atomic approval signatures.
- **Atomic State & Resume:** Idempotent execution powered by content-addressed artifact hashing, `state.json`, and `events.jsonl`.
- **Strict Modularity Rule:** Every module, pipeline, and block is strictly constrained to `< 200 lines` of code.

---

## The Unix Block Contract

Each block is an isolated executable process communicating exclusively via the filesystem:

```
Inputs Directory (--in)          Outputs Directory (--out)
├── <artifact_1>.json    ===>    ├── <output_artifact>.json
├── <artifact_2>.patch  [BLOCK]  ├── <output_artifact>.patch
└── _retry.json                  └── _status.json (Exit state + metrics)
```

### Exit Codes
- `0`: Success (`ok`)
- `10`: Retryable failure (`retryable`, backoff applied)
- `20`: Fatal failure (`failed`)
- `30`: Human approval required (`needs_human` / `paused_gate`)

---

## Pipeline Execution Flow

```
[Target Repo] ──> agyflow init ──> agyflow run pipelines/full.yaml
                         │
                         ├── (1) Locks tests & creates read-only context
                         ├── (2) Hunts defects & generates reproducer scripts
                         ├── [PAUSE: Gate 1] ──> agyflow approve findings
                         ├── (3) Designs distinct approaches (A, B)
                         ├── [PAUSE: Gate 2] ──> agyflow approve plan --pick A
                         ├── (4) Spawns worktree 'act-A', edits code, runs pytest
                         ├── (5) Judges test stability across seeds & ranks candidates
                         ├── [PAUSE: Gate 3] ──> agyflow approve candidate --pick A
                         └── (6) Merges patch into branch 'agyflow/<run_id>'
```

---

## CLI Usage

### 1. Initialize a Repository
```bash
python3.11 -m agyflow init --template generic
```
Creates `.flow/project.toml`, `.flow/decisions.md`, and sets up gitignore rules.

### 2. Run a Pipeline
```bash
python3.11 -m agyflow run pipelines/full.yaml
```

### 3. Review & Approve Gates
```bash
# Check status and view pending gates
python3.11 -m agyflow status

# Approve gate with selected approaches or findings
python3.11 -m agyflow approve findings --accept F-001 --note "Approved defect"
python3.11 -m agyflow approve plan --pick A --note "Selected approach A"
python3.11 -m agyflow approve candidate --pick A --note "Approved candidate"
```

### 4. Resume an Interrupted Run
```bash
python3.11 -m agyflow resume
```

### 5. Diagnostics
```bash
python3.11 -m agyflow doctor
```

---

## Project Structure

```
agyflow/
├── blocks/                 # 28 Section 4 Unix blocks
│   ├── agent-call/         # Modular LLM / Antigravity CLI adapter
│   ├── diff-guard/         # Tamper, test, dependency guard
│   ├── judge-run/          # Multi-seed test execution & metrics
│   ├── merge-apply/        # Non-destructive Git branch committer
│   ├── patch-export/       # Worktree binary diff extractor
│   ├── pick-item/          # JSON array / collection extractor
│   └── rank/               # Composite candidate scorer
├── pipelines/              # Declarative YAML workflow DAGs
│   ├── full.yaml           # End-to-end full repair lifecycle
│   ├── preflight.yaml      # Snapshot, test locking & context build
│   ├── analyze.yaml        # Defect discovery & evidence validation
│   ├── plan.yaml           # Multi-approach planning & research
│   └── act.yaml            # Parallel worktree act, judge & rank
├── src/agyflow/            # Core engine
│   ├── cli.py              # CLI entrypoint & subcommands
│   ├── engine/             # Runner, store, scope, DAG parser, policy
│   └── lib/                # Process runner, schema, git utilities
└── tests/                  # 100% automated test coverage
    ├── contract/           # 112 block Unix contract tests
    ├── e2e/                # Full workflow acceptance test
    └── engine/             # Unit tests for engine components
```

---

## Verification & Testing

```bash
# Run full unit and acceptance test suite (74 tests)
python3.11 -m pytest

# Run Unix block contract test suite (112 tests)
python3.11 -m pytest tests/contract/run_all.py

# Verify strict < 200 line constraint across all project files
python3.11 -c "
import os, sys
bad = [f for r, _, fs in os.walk('.') if not any(x in r for x in ['.git', '.pytest_cache', '__pycache__'])
       for f in fs if f.endswith(('.py', '.toml', '.yaml', '.md'))
       if sum(1 for _ in open(os.path.join(r, f), errors='ignore')) >= 200]
sys.exit(f'Files >= 200 lines: {bad}' if bad else 0)
"
```

---

## Tech Stack & Architecture

- **Language:** Python 3.11 (strict)
- **VCS Automation:** Git 2.x (Worktrees, Binary Diffs, Detached HEADs)
- **Process Isolation:** POSIX Process Groups (`setpgid`, `SIGTERM`/`SIGKILL` escalation)
- **Validation:** JSON Schema Draft 7 & Canonical JSON Hash Verification
- **Testing:** Pytest 8.x with Subprocess & Fake Agent Harnesses
