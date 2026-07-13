## PR Review Governance

Use FAST_PATH only for small, low-risk, complete, understandable PRs; use standard review for broader uncertainty and block high-risk, unsafe, incomplete, ambiguous, or unauthorized work. Check metadata, scope, patch evidence, verification, checks, risk paths, and human objections without dumping raw payloads or full diffs.

Report one outcome: `APPROVED_FOR_AUTOMERGE`, `APPROVED_FOR_HUMAN_MERGE`, `CHANGES_REQUESTED`, `RISK_REVIEW_REQUIRED`, or `BLOCKED`; the GitHub review comment names the exact reviewed commit. Approval requires an eligible, non-draft mapped PR, acceptable verification, no secrets/high-risk files/failed checks/objection, and matching head SHA. Risk/block outcomes remain human/CEO gates. Create only the outcome-required next task; never merge or create QA/deploy/release/post-merge work.
