# Changelog

All notable changes to ncloud-mcp-server (formerly ncp-mcp) are listed here, newest first.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
While the version is 0.x, a new minor version (0.x → 0.(x+1)) may need changes in
your configuration. Those entries are marked **Breaking**.

## [Unreleased]

## [0.7.0] - 2026-11-19

### Changed
- **Breaking:** the package and its command are renamed from `ncp-mcp` to `ncloud-mcp-server`.
  The Python module stays `ncp_mcp`.
- **Breaking:** `ncp_call` and `ncp_batch` are merged into one read tool, `ncp_read`. It takes
  `calls`, a list of 1 to 50 items with the same keys as before (`service`, `action`, and
  optionally `params`, `fields`, `paginate`, `profile`), and always returns
  `{count, failed, results}`. A single call gets the whole result budget.
  Tools are now split only by effect (read or write), which is how MCP clients grant permissions.
  To migrate:
  - `ncp_call(service=..., action=..., ...)` becomes `ncp_read(calls=[{"service": ..., "action": ..., ...}])`.
  - `ncp_batch(calls=[...])` becomes `ncp_read(calls=[...])`.
  - In client permission settings, replace `mcp__<name>__ncp_call` and `mcp__<name>__ncp_batch`
    with `mcp__<name>__ncp_read`, where `<name>` is the name you registered (Claude Code naming; other clients differ).

### Added
- Releases are published to PyPI as `ncloud-mcp-server` by the `tag-release` workflow
  (Trusted Publishing, no API token).
- README: one-line install for Claude Code (`claude mcp add ... --scope user`) with the scope options.
- `CHANGELOG.md`, and a release step in `CONTRIBUTING.md` to keep it updated.

### Fixed
- The credential config is read once per `ncp_read` request instead of once per item.
  Before, a 50-call `ncp_batch` read `~/.ncloud/configure` 50 times, and an edit made while it
  ran could give its items different profiles. Each request still reads the config, so key
  changes apply without a restart.

## [0.6.0] - 2026-11-19

### Added
- Result size cap: `NCP_MCP_MAX_RESULT_CHARS` (default 100,000 characters of JSON).
  List responses keep the leading items that fit, other responses become cut JSON text.
  `truncated` says why and suggests `fields`. `ncp_batch` splits the budget across its
  calls (at least 5,000 each).
- `CONTRIBUTING.md`: setup, how the guard is verified, how to refresh the SDK snapshot,
  the NCP API facts the code relies on, and the release steps.
- `tests/data/ncp_sdk_apis.txt` (676 APIs from the official Go SDK), `scripts/refresh_sdk_apis.sh`,
  and tests that every read passes, every write and state-changing GET is blocked, and the
  list of credential-returning reads is exact.

### Changed
- **Breaking:** `NCP_WRITE_ALLOWLIST` entries take an optional method: `METHOD service:action`.
  Without a method only GET is allowed, so allowing POST on a REST path no longer allows
  DELETE on it. REST entries that were used with POST/PUT/DELETE must now name the method.
  The block message shows the entry to add.
- Shared-gateway actions accept GET and POST alike (checked against the API docs and the SDK),
  so an entry such as `vserver:stopServerInstances` allows both. REST services keep distinct methods.
- Paging parameters (`pageNo`, `pageSize`, `page`, `size`) are validated before any request.
  A bad value gives a clear error instead of "internal error".

### Fixed
- `paginate` must be a JSON boolean. `ncp_batch` treated the string `"false"` as true.
- `GET /timeZone` (sourcepipeline, vsourcepipeline) is a read but was blocked because
  "time" looked like a verb.

## [0.5.0] - 2026-11-19

### Added
- `README.en.md` and language links between the two READMEs.

### Changed
- **Security:** deny-by-default read-only guard. A shared-gateway action counts as read-only
  only when it is a single verb-first camelCase segment with a read verb. Lowercase, PascalCase
  or nested variants (`terminateserverinstances`, `TerminateServerInstances`,
  `terminateServerInstances/x`) were classified as reads before and are now treated as writes.
- **Security:** a REST-style path is a write if any segment, not only the last one, starts
  with a non-read verb. vcdss and vses change state over GET (`restartKafkaService`,
  `stopDataImportJob`, ...), which the old last-segment rule let through `ncp_call`.
- `getRootPassword`, `getRootPasswordServerInstanceList` and the NKS `kubeconfig` return
  credentials. `ncp_call` and `ncp_batch` refuse them. They can be called only through
  `ncp_write` with an `NCP_WRITE_ALLOWLIST` entry.

## [0.4.0] - 2026-11-19

### Added
- Per-zone service allowlist built from the official NCP API docs and SDK
  (government Sub Account uses `/api/v2`, financial billing has its own host, STS is public zone only).
- `ncp_write` also needs an `NCP_WRITE_ALLOWLIST` `service:action` entry.
- Read-only calls retry on 429, 5xx and timeouts with backoff and `Retry-After`.
  Writes are never retried and report an unknown outcome on timeout.
- Process-wide concurrency limit, configurable timeout and retries.
- Input validation and per-item isolation in `ncp_batch`.
- Mocked-transport tests (45 at the time).
- `tag-release` workflow: tags `main` as `v<version>` when the tag is missing.

### Fixed
- Paging follows verified rules: `pageNo` 0 and 1 both mean the first page, and with `pageNo`
  the `totalRows` is the page count. Lists are refetched page by page and `paging.complete`
  is reported. Sub Account v1 uses `hasNext` paging.
- An incomplete first page is reported for other list shapes (`totalElements` etc.).

## [0.3.0] - 2026-11-19

No git tag exists for this version.

### Changed
- **Breaking:** profiles need an explicit `ncloud_zone` and a name ending with `-<zone>`.
  Profiles that break the rules are disabled and reported in `ncp_profiles`.
- **Breaking:** `profile` is required when more than one profile exists. There is no default profile.
- Valid profile names are fixed into the tool schema at startup.

### Added
- `ncp_profiles(verify=true)` shows which account and user a key belongs to (STS `caller-identity`).

## [0.2.0] - 2026-11-19

### Changed
- **Breaking:** `ncp_call` and `ncp_batch` are read-only. Changes go through `ncp_write`,
  which is registered only when the server starts with `NCP_ALLOW_WRITE=true`.

### Added
- MCP tool annotations (`readOnlyHint`, `destructiveHint`).
- `fields` option keeps only the selected keys of list items (dot paths).

## [0.1.0] - 2026-11-19

### Added
- First release: `ncp_profiles`, `ncp_call` and `ncp_batch`.
- Signature v2 (HMAC-SHA256) and `~/.ncloud/configure` profiles for the public, government and financial zones.
- Read-only by default, decided by action name. `NCP_ALLOW_WRITE` enables writes.
- Automatic paging and a gateway host allowlist.
- gitleaks pre-commit hook and GitHub Actions (gitleaks, pytest).

[Unreleased]: https://github.com/revert12/ncloud-mcp-server/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/revert12/ncloud-mcp-server/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/revert12/ncloud-mcp-server/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/revert12/ncloud-mcp-server/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/revert12/ncloud-mcp-server/compare/v0.2.0...v0.4.0
[0.2.0]: https://github.com/revert12/ncloud-mcp-server/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/revert12/ncloud-mcp-server/releases/tag/v0.1.0
