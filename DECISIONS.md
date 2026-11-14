# Decision Record

## 2026-11-14: Initial Baseline Decisions
- **D1:** Python 3.11+, pyyaml + jsonschema only. TOML via stdlib tomllib.
- **D2:** Blocks are CLI programs (`--in`, `--out`, `--cfg`), Python by default.
- **D3:** Agents are config (role prompt + schema + profile) of generic agent-call.
- **D4:** Artifacts are JSON files with envelopes on disk.
- **D5:** Each agent call is fresh `agy --print` (no resumed sessions).
- **D6:** Timeouts and process group killing implemented natively in Python.
- **D7:** Read-only roles run in throwaway `chmod a-w` worktree.
- **D8:** Engine owns locking, budgets, retries, loops, state, output schema validation.
- **D9:** Launcher interface defaults to Subprocess; HerdrLauncher is M6 cockpit.
- **D10:** Three human gates (G1 findings, G2 plan, G3 candidate) bound to content hashes.
