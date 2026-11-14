# agyflow Operator Guide

This guide covers daily operations, human gate reviews, failure resolution, and system maintenance.

---

## 1. Daily Commands

### Initialization
Scaffold a new project in the current git repository:
```bash
agyflow init --template generic  # or --template ml
```
Creates `.flow/project.toml` and initializes git-ignored `.flow/runs/`, `.flow/worktrees/`, and `.flow/locks/`.

### Running Pipelines
Start an autonomous DAG run:
```bash
agyflow run pipelines/full.yaml
agyflow run pipelines/full.yaml --wait-gate  # auto-resumes once gate files are written
agyflow run pipelines/act.yaml --launcher subprocess  # or --launcher herdr
```

### Checking Status
Inspect active runs, completed steps, pending gates, and resource counters:
```bash
agyflow status
agyflow status <run_id>
```

### Resuming Workflows
Resume a run after gate approval or transient failure:
```bash
agyflow resume
agyflow resume <run_id> --wait-gate
```

---

## 2. Human Gate Decisions (G1, G2, G3)

When a pipeline pauses at a human gate, it writes `<flow>/runs/<run_id>/gates/<gate>.review.md` and exits code 30.

### G1 (Findings Review)
Inspect repro evidence and accept or reject candidate defects:
```bash
agyflow approve findings --accept fp_101,fp_102 --reject fp_103
agyflow reject findings --note "Defect claims do not align with current sprint goals"
```

### G2 (Plan Approval)
Select which candidate approaches to implement:
```bash
agyflow approve plan --pick A,B
agyflow reject plan --note "Propose alternative data-driven approaches"
```

### G3 (Candidate Selection & Merge)
Select the winning candidate patch to commit to branch `agyflow/<run_id>`:
```bash
agyflow approve candidate --pick A
agyflow reject candidate --note "Reviewer noted regression risks; abandon attempt"
```

---

## 3. Resolving `NEEDS_HUMAN`

When an uncommitted step with dirty worktree side effects is interrupted by crash, `agyflow` pauses with `needs_human`.

### Options:
- **`--discard`**: Deletes the corrupted worktree and re-runs the step cleanly:
  ```bash
  agyflow resolve act-A --discard
  agyflow resume
  ```
- **`--accept`**: Adopts the current worktree state as synthetic commit:
  ```bash
  agyflow resolve act-A --accept
  agyflow resume
  ```

---

## 4. Maintenance & Cockpit

### Garbage Collection (`gc`)
Deletes expired runs and dangling worktrees, strictly bounded within `.flow/runs` and `.flow/worktrees`:
```bash
agyflow gc
```

### State Reconstruction (`repair`)
Rebuilds corrupted or unparsable `state.json` from disk step outputs and events:
```bash
agyflow repair <run_id>
```

### Doctor Probes
Audit your local environment for CLI tools, flags, and model capabilities:
```bash
agyflow doctor
agyflow doctor --live
```
