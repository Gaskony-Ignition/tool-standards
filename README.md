# Ignition Standards

**A written standard for Ignition 8.3 projects, and a lint gate that enforces it on every push.**

> **Not an Inductive Automation product, and not supported by Inductive Automation.** Independent work, largely built with AI tools and tested for one purpose on a limited subset of gateway versions and platforms. Take the ideas; fork and review it before it goes near production. Feedback is welcome in Issues; improvements are made where possible, but no support is guaranteed. [NOTICE.md](NOTICE.md) says more.

## Why this exists

A lint gate that only runs on one workstation checks nobody else's push. And "good Ignition practice" was unwritten, so a failed check had no rule behind it to point at.

## What it looks like

```
views/Overview/view.json:1: V1 root/Header/Label_0 has a Designer default name
...
lint-gate: FAIL - 2 new, 41 baseline, 1 excepted
```

## What it does

| Part | What it is |
| --- | --- |
| `STANDARD.md` | The rules, each with the check ID that enforces it or `review` |
| `gate/` | ign-lint, pylint and `ign-check.py` run over a repo |
| Reusable workflow | Runs the gate in CI; callers use `caller-example.yml` |
| `lint.json` | Per-repo exceptions, each with a reason |
| `lint-baseline.json` | Findings already present, so only new ones fail |

## How to use it

1. Copy `caller-example.yml` to `.github/workflows/lint.yml` in your repo.
2. Run the gate locally (needs `uv`):

   ```
   gate/lint-gate.sh <repo>
   ```

3. On an existing repo, record today's findings once so only new ones fail:

   ```
   gate/lint-gate.sh <repo> --write-baseline
   ```

4. To accept a finding, add an exception to the repo's `lint.json` with `file`, `rule`, `match` and `reason`.

## Licence

Apache-2.0. See [LICENSE](LICENSE).
