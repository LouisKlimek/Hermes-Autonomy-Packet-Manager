## GitHub PR Automation

Mode: `AUTO_REVIEW_AND_AUTOMERGE_ENABLED_LOW_TOKEN`. Pull requests may be automatically reviewed and merged only by other profiles after their gates pass.

- Never push directly to `main`, `master`, `production`, or `release`; use `agent/full-stack-developer/` branches and keep each PR focused.
- GitHub writes required by this pipeline (create/update the task PR, required review comment, approved auto-merge) are authorized. Use authenticated `gh`, or the documented authenticated REST fallback. Report only real credential, permission, network, or tool-denial blockers precisely and without secrets.
- Include this metadata in every PR:

  ```markdown
  <!-- hermes:board=opportunity-discovery -->
  <!-- hermes:task=<kanban-task-id> -->
  <!-- hermes:profile=fullstack-developer -->
  <!-- hermes:merge-policy=auto-if-approved -->
  <!-- hermes:review-policy=pr-reviewer-required -->

  ## Hermes
  Task: <kanban-task-id>
  Board: opportunity-discovery
  Assignee: fullstack-developer
  Merge Policy: Auto if approved
  Review Policy: PR reviewer required
  Auto Merge: Eligible / Not Eligible
  ```

  Mark Eligible only for focused low/medium-risk work.
- Do not report a PR update complete until `git ls-remote origin <branch>` exactly equals `git rev-parse HEAD`. If the branch was rewritten, push with `git push --force-with-lease origin <branch>`.
- After a verified push, create/link a `pr-reviewer` child task for the same PR head SHA. Its body must state the repository, PR URL, source task, board, `Current Head SHA`, `AUTOMATED_REVIEW_FOR_AUTOMERGE_LOW_TOKEN`, review-only policy, outcomes `APPROVED_FOR_AUTOMERGE`, `APPROVED_FOR_HUMAN_MERGE`, `CHANGES_REQUESTED`, `RISK_REVIEW_REQUIRED`, `BLOCKED`, and require the review comment `Reviewed Commit: <sha>`.
- If push or verification is blocked by approval, credentials, permissions, network, or an interactive human-only step: stop, do not create a stale review task or duplicate PR, and report `BLOCKED_ON_HUMAN_PUSH` with repository, branch, checkout path, local/remote SHAs, required push command, and blocker. Otherwise report `READY_FOR_REVIEW` only after the remote SHA is verified, including PR URL, local/remote heads, review task, risk, and verification.
- On `CHANGES_REQUESTED`, update the same branch and renew the verified-head review handoff. On `RISK_REVIEW_REQUIRED` or `BLOCKED`, wait for a newly assigned task.
