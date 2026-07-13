## GitHub Auto-Merge Governance

Auto-merge only an allowed, open, non-draft PR with an allowed base and agent branch, verified remote head, complete Hermes metadata, `Auto Merge: Eligible`, an APPROVED_FOR_AUTOMERGE review whose `Reviewed Commit` exactly equals the current head, passing required checks, mergeability, branch-protection compliance, no high-risk files, and no later objection, block, risk finding, or stale-branch evidence.

Use compact snapshots and a dry run before a squash merge. Any missing, stale, blocked, uncertain, or mismatched condition means do not merge; preserve the branch and report the shortest useful reason. Never expose credentials. After a verified merge, attempt safe agent-branch cleanup and end the task; create no QA, deploy, release, or extra follow-up task unless explicitly requested.
