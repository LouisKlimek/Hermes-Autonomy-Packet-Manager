# GitHub addon and repository-policy migration

`github-agent` is the sole GitHub addon for `ceo-orchestrator`,
`fullstack-developer`, `pr-reviewer`, and `github-manager`. It is explicitly
incompatible with the four deprecated, role-specific GitHub/repository addons.
A profile without `github-agent` is denied GitHub repository access.

## Canonical policy

The only repository allowlist is `$HERMES_HOME/hapm_policies/repo_allowlist.json`:

```json
{"version": 1, "repositories": ["owner/repository"]}
```

A missing or empty policy is default-deny. Entries must be canonical
`owner/repository` strings. The policy engine validates every add, removal, and
replacement and writes atomically with mode `0600`. It does not read or store
credentials.

## Migration and rollback

Before enabling `github-agent`, run `migrate_legacy_allowlists()` with the
legacy addon prose files as sources. The migration extracts only valid
repository identifiers, merges them with the canonical policy, and is
idempotent. If it changes an existing policy, it creates a one-time `*.bak`
backup before replacement. Restore that backup atomically to roll back the
policy. After migration, disable the four deprecated addons using the existing
reversible HAPM toggle path, then enable `github-agent`.

Do not edit profile `.env` files as part of this migration.
