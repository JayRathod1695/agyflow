# agyflow

A swappable-block, multi-agent engineering workflow runner built for the agy CLI (Gemini 3.8 Flash), with herdr as the cockpit.

## Architecture
- Small, independent CLI blocks.
- One uniform contract: `--in <dir> --out <dir> --cfg <cfg>`.
- Content-addressed artifact exchange on disk.
- Zero agent trust: deterministic scripts guard and judge all agent artifacts.
- Non-destructive git: all modifications isolated in throwaway git worktrees.
