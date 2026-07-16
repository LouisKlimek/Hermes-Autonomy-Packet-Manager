# GitHub addon and repository-policy migration

`github-agent` is the sole GitHub addon for `ceo-orchestrator`,
`fullstack-developer`, `pr-reviewer`, and `github-manager`. A profile without
that active addon is denied GitHub access.

## Canonical policy and runtime enforcement

The only allowlist is `$HERMES_HOME/hapm_policies/repo_allowlist.json`:

```json
{"version": 1, "repositories": ["owner/repository"]}
```

A missing or empty policy is default-deny. Every runtime GitHub API/operation
must resolve its configured origin and call `require_repository_allowed()`
before any Git command or GitHub request. The check requires both an active
`github-agent` profile and an exact canonical policy entry; malformed state is
denied.

Repository-policy writes are human-only: the routes accept a principal only
from server-populated dashboard authentication state and permit the configured
`HAPM_POLICY_ADMINS` identities (default `ceo-orchestrator`). Request headers
are never treated as an authenticated identity. All three mutation routes are
covered by server-side authorization tests.

## Idempotent reconciliation and authorized rollback

Use `reconcile_legacy_github_addons()` rather than manually toggling addons.
It inventories active deprecated GitHub addons for the specified profiles,
extracts repository identifiers only from those active addon sources, and
migrates **only** the caller's explicit approved-repository list. It disables
the legacy addons through the normal reversible HAPM toggle path and enables
`github-agent`.

Before the first or any changed reconciliation, it writes an unlimited-retention
Fernet-encrypted rollback artifact under
`$HERMES_HOME/hapm_policies/rollback_backups/`, with directory mode `0700` and
artifact mode `0600`. The encryption key is supplied only to the server-side
call; no plaintext fallback exists. The artifact is opaque and contains no
returned secret value. On any policy/toggle failure, reconciliation restores the
policy and each profile's `SOUL.md`, `hapm.lock`, and `.hapm` state from the
encrypted snapshot, then fails the request. Authorized rollback requires the
same server-side key and backup artifact; backups have no automatic expiry.

Do not edit profile `.env` files as part of this migration.
