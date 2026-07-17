# Live SOUL Source Inventory

This registry was re-derived from the live source files below, not from a prior HAPM preset or pull request. Each substantive source has exactly one preset. The base `SOUL.md` is role-only; the matching `*-governance` addon contains the source-derived selectable operating policy.

## Represented profiles

- ceo-orchestrator — `/opt/data/profiles/ceo-orchestrator/SOUL.md`
- default — `/opt/data/profiles/default/SOUL.md`; substantive general-assistant role, represented without an operating-policy addon
- designer — `/opt/data/profiles/designer/SOUL.md`
- evidence-quality-analyst — `/opt/data/profiles/evidence-quality-analyst/SOUL.md`
- fullstack-developer — `/opt/data/profiles/fullstack-developer/SOUL.md`
- github-manager — `/opt/data/profiles/github-manager/SOUL.md`
- memory-curator — `/opt/data/profiles/memory-curator/SOUL.md`
- opportunity-strategy-agent — `/opt/data/profiles/opportunity-strategy-agent/SOUL.md`
- pr-reviewer — `/opt/data/profiles/pr-reviewer/SOUL.md`
- prd-bridge — `/opt/data/profiles/prd-bridge/SOUL.md`
- prd-task-planner — `/opt/data/profiles/prd-task-planner/SOUL.md`
- qa-auditor — `/opt/data/profiles/qa-auditor/SOUL.md`
- technical-feasibility-reviewer — `/opt/data/profiles/technical-feasibility-reviewer/SOUL.md`

## Shared policy ownership

- `addons/yagni` is the sole owner of YAGNI wording.
- `addons/postmerge-no-qa-deploy` is the shared owner of the ordinary `MERGE_ONLY_NO_QA_NO_DEPLOY` boundary.
- Other source-derived operating policies are profile-targeted to avoid claiming reuse where their detailed guardrails differ.

## Explicit exclusions

None. The stale `presets/prd-bridge-old` entry and all addons tied only to that retired profile were deliberately removed.
