# Contributing

Issues and pull requests are welcome. Issues may be written in Korean or English.

## Setup

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/) 0.13 or newer
(`uv.lock` uses the 0.13 lockfile format; older uv refuses to run, see `[tool.uv]` in `pyproject.toml`).

```sh
uv sync
uv run pytest
git config core.hooksPath .githooks   # run the gitleaks secret scan before each commit
```

The pre-commit hook needs [gitleaks](https://github.com/gitleaks/gitleaks) on your `PATH`
(e.g. `winget install Gitleaks.Gitleaks`, `brew install gitleaks`); without it, commits are blocked.
CI runs gitleaks and `uv sync --locked` too, so a stale `uv.lock` fails the build.

## Read-only guard: how it is verified

`ncp_call` / `ncp_batch` must never change anything, so `src/ncp_mcp/guard.py` only lets
through actions that are **proven** read-only. Read its module docstring first; in short:

- Shared-gateway services (`/<service>/v2/<action>`): a read is one camelCase segment
  starting with a read verb (`getServerInstanceList`). The gateway takes GET and POST alike.
- REST-style services: a write if any path segment starts with a non-read verb.
  Some of these change state over GET (e.g. `vcdss /cluster/restartKafkaService/{id}`).
- Reads that return credentials (`getRootPassword`, NKS `kubeconfig`) are blocked too.

These rules are checked against **every API in the official SDK**:

| File | Purpose |
|---|---|
| `tests/data/ncp_sdk_apis.txt` | Snapshot of all APIs in [ncloud-sdk-go-v2](https://github.com/NaverCloudPlatform/ncloud-sdk-go-v2) (service, method, path) |
| `tests/test_sdk_apis.py` | Every read passes, every write and state-changing GET is blocked, the credential list is exact |
| `scripts/refresh_sdk_apis.sh` | Rebuilds the snapshot from the latest SDK |

### Refreshing the SDK snapshot

Do this when NCP adds services or APIs, or before a release that touches the guard.

```sh
sh scripts/refresh_sdk_apis.sh        # Git Bash on Windows
uv run pytest tests/test_sdk_apis.py
```

If tests fail, decide for each API named in the failure:

| The API... | Then |
|---|---|
| is a read the guard blocks | fix `guard.py` (e.g. add the noun to `NOUN_SEGMENTS`), or add it to `BLOCKED_READS` if blocking it is intended |
| changes state over GET | add it to `STATE_CHANGING_GETS` (the guard should already block it) |
| returns secrets | add its segment to `guard.SENSITIVE` and the pair to `SENSITIVE` in the test |
| belongs to a new service | add the service to `src/ncp_mcp/endpoints.py` with its documented endpoint |

Commit the refreshed snapshot together with these changes.

### Not covered by the snapshot

The SDK does not include `billing`, `subaccount`, `sts`, or the government (`gov`) and
financial (`fin`) zone endpoints. Changes to those need a check against the API docs
([api.ncloud-docs.com](https://api.ncloud-docs.com/docs/en/home)); cite the page in the PR.

## NCP API facts this code relies on

Checked against the API docs. When one of them turns out to be wrong, fix the code and this table.

| Fact | Source |
|---|---|
| Shared-gateway v2 actions accept both GET and POST, reads and writes alike | [getServerInstanceList](https://api.ncloud-docs.com/docs/en/compute-vserver-server-getserverinstancelist), [stopServerInstances](https://api.ncloud-docs.com/docs/compute-vserver-server-stopserverinstances): method `GET \| POST` |
| `pageNo` is 0–N, and 0 and 1 both mean the first page; `pageSize` is 1–1000 and required with `pageNo` | getServerInstanceList request parameters |
| `totalRows` is the total count; without `pageNo` the whole list is returned (live calls show it counts only the page when `pageNo` is given) | [ServerInstanceList type](https://api.ncloud-docs.com/docs/en/common-vapidatatype-serverinstancelist); see the `client.py` docstring |
| Sub Account v1 list: `page` starts at 0, `size` is 1–N (default 10); the response has `items`, `hasNext`, `totalItems` | [Get sub account list](https://api.ncloud-docs.com/docs/en/management-subaccount-getsubaccounts) |

## Releases

Bump the version in both `pyproject.toml` and `src/ncp_mcp/__init__.py`, run `uv lock`,
and update the install tag in `README.md` and `README.en.md`. After the PR is merged,
the `tag-release` workflow tags `main` as `v<version>`.

Use a new minor version (0.x → 0.(x+1)) when users must change their configuration,
for example the format of `NCP_WRITE_ALLOWLIST`.
