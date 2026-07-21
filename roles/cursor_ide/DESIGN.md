<!-- SPDX-License-Identifier: MIT-0 -->
# cursor_ide — Design Notes

## Accepted risk: no checksum verification on the Cursor `.deb` download

Unlike `rtk`, `beads`, and `claude_code` (see
`docs/architecture/concepts/checksum-verification-pattern.md`), this role
downloads and installs Cursor's `.deb` package with no checksum
verification.

Investigated and ruled out, live, before accepting this as a known gap:

- The download URL (`https://api2.cursor.sh/updates/download/golden/linux-{arch}-deb/cursor/`)
  redirects to a versioned, commit-hash-scoped path on
  `downloads.cursor.com` (e.g.
  `.../production/<commit>/linux/x64/deb/amd64/deb/cursor_<version>_amd64.deb`)
  — so the resolved artefact is a real, specific build, not a moving
  target at fetch time.
- No sidecar checksum (`<file>.sha256`, `<file>.sig`) exists next to that
  artefact — the storage bucket returns 403 for both.
- No `electron-builder`-style aggregate manifest (e.g. `latest-linux.yml`)
  is published alongside it.
- Cursor's own update-check API rejects unauthenticated/guessed request
  shapes and was not pursued further; Cursor's public download and
  changelog pages do not publish checksums either.

Conclusion: Cursor does not publish any checksum source this role can
verify against today. Pinning a specific build and computing our own
checksum (mirroring `obsidian`'s `obsidian_sha256_amd64` pattern) was
considered and rejected — it would require abandoning the "always
install latest" convenience this role currently has, a larger behavior
change than this integrity gap justifies on its own.

This is an accepted risk, tracked separately from
`docs/architecture/technical-debt/technical-debt.md`'s TD-003 (which
covers *unpinned versions*, not *unverified integrity* — a distinct
axis). Revisit if Cursor ever publishes a checksum source, or if this
role's install approach changes for other reasons.
