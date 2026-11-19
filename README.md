# ncloud-mcp-server

[한국어](https://github.com/revert12/ncloud-mcp-server/blob/main/README.ko.md) | **English**

A lightweight MCP server for NAVER Cloud Platform (Ncloud).

Instead of one tool per API, a **few signed, generic call tools** reach every Ncloud API.
The tool definitions are small, so connecting the server costs almost no conversation context.

> This is not an official NAVER Cloud project.

## Tools

| Tool | Description |
|---|---|
| `ncp_profiles` | Usable profiles and zones, profiles disabled by the rules (with the reason), and whether writes are enabled. With `verify=true`, shows who owns each key (account ID, login alias, main/sub account). Secrets are never returned |
| `ncp_read` | 1 to 50 **read-only** API calls, run 5 at a time. Handles signing, `responseFormatType=json`, list parameter expansion, automatic paging, and `fields` to return only the fields you need. A failed item does not affect the others |
| `ncp_write` | Calls APIs that create, change or delete resources. **Registered only when the server runs with `NCP_ALLOW_WRITE=true`** |

```jsonc
// Server list (only the fields you need)
ncp_read(calls=[{"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "KR"},
                 "fields": ["serverName", "serverInstanceStatus.code", "privateIp"]}])

// Sub accounts (REST-style service)
ncp_read(calls=[{"service": "subaccount", "action": "/sub-accounts"}])

// Several regions at once
ncp_read(calls=[
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "KR"}},
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "JPN"}}
])
```

## Safety

- **Reads and writes are separate tools.** `ncp_read` accepts only read actions such as
  `get*`, `list*` and `describe*`, plus REST-style GETs. Ncloud's shared API gateway accepts even
  delete actions over GET, so operations are classified by **action name**, not by HTTP method.
- Only actions **proven read-only** are allowed. On shared-gateway services an action counts as a read
  only if it is a single path segment in camelCase starting with a read verb (`getServerInstanceList`).
  Anything else, such as `terminateserverinstances`, `TerminateServerInstances` or
  `terminateServerInstances/x`, is treated as a write and blocked.
- **Read actions that return secrets** (`getRootPassword`, `getRootPasswordServerInstanceList`, the NKS
  `kubeconfig`) are blocked in `ncp_read`. If you really need one, add it to
  `NCP_WRITE_ALLOWLIST` and call it with `ncp_write`.
- Writes are possible only through `ncp_write`, which appears only when the server runs with
  `NCP_ALLOW_WRITE=true`. Each action must also be listed in `NCP_WRITE_ALLOWLIST` (comma separated).
  Write requests are never retried.
  - For shared-gateway services, write `vserver:stopServerInstances`. The shared gateway takes
    GET and POST as the same call, so both are allowed.
  - For REST-style services, put the method first: `POST subaccount:/sub-accounts`,
    `DELETE subaccount:/sub-accounts/123`. An entry without a method allows GET only,
    and allowing POST on a path does not allow DELETE on it.
  - If you used an entry like `subaccount:/sub-accounts` for POST with 0.5.x or earlier, add the method.
- Callable services are limited to a per-zone allowlist (`src/ncp_mcp/endpoints.py`, based on the official
  NCP API docs). To use a service that is not listed, add it explicitly, e.g. `NCP_MCP_EXTRA_SERVICES="gov:cdn"`.
- Read requests are retried up to 2 times on 429, 5xx and timeouts (`NCP_MCP_RETRIES`), and concurrent
  requests are limited to 5 (`NCP_MCP_CONCURRENCY`).
- A single result is capped at 100,000 characters of JSON, about 25,000 tokens (`NCP_MCP_MAX_RESULT_CHARS`).
  Beyond that, only the first list items are kept and `truncated` explains why. With several calls, `ncp_read` splits this
  budget across them. If a result was cut, use `fields` to keep only the columns you need, or narrow the query.
- Every tool carries the standard MCP hints (`readOnlyHint` / `destructiveHint`). In your client's
  permission settings we recommend **allowing `ncp_read`, and asking every time for `ncp_write`**.
  (Claude Code example: `mcp__ncloud__ncp_read` in `permissions.allow`, `mcp__ncloud__ncp_write` in `permissions.ask`)
- Signed requests are sent only to the selected zone's API gateway domains (`*.apigw.ntruss.com` etc.).
- Keys never appear in error messages or results. The final permission boundary is the sub-account
  permissions of the access key, so we recommend **a sub-account key with read-only permissions**.

## Installation

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

**Claude Code**: register it with one command.

```sh
claude mcp add ncloud --scope user -- uvx --from git+https://github.com/revert12/ncloud-mcp-server@v0.7.0 ncloud-mcp-server
```

`--scope` picks where it is registered.
- `local` (default): only you, only in the current project (stored in `~/.claude.json`)
- `user`: only you, in every project
- `project`: stored in `.mcp.json` at the project root and shared with your team

Check with `claude mcp list`; remove with `claude mcp remove ncloud`.

**Other MCP clients**: add it to the config file directly.

```jsonc
// .mcp.json
{
  "mcpServers": {
    "ncloud": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/revert12/ncloud-mcp-server@v0.7.0", "ncloud-mcp-server"]
    }
  }
}
```

Pinning a version with a tag is recommended.

## Credentials

The server reads `~/.ncloud/configure` (INI), the same file the Ncloud CLI uses. Each section is one profile.

```ini
[mycompany-pub]
ncloud_access_key_id = <access key>
ncloud_secret_access_key = <secret key>
ncloud_zone = pub

[customer-a-gov]
ncloud_access_key_id = <gov access key>
ncloud_secret_access_key = <gov secret key>
ncloud_zone = gov
```

**Profile rules** (a profile that breaks one is not used; `ncp_profiles` lists it under `disabled` with the reason)
- `ncloud_zone` (`pub`, `gov` or `fin`) is required.
- Profile names use lowercase letters, digits and hyphens, and **must end with the zone** (`-pub`, `-gov`, `-fin`),
  so the zone is visible from the name alone. For the same reason `[DEFAULT]` cannot be used.
- With two or more profiles, every call must name its `profile`. There is no default profile.
- Usable profile names are written into the tool definitions as choices when the server starts.
  Restart the MCP server after changing profiles.
- `ncp_profiles(verify=true)` asks STS (`caller-identity`) which NCP account and user each key really belongs to.

The Ncloud CLI can use the same profiles with `--profile <name>`.

- Setting the environment variables `NCLOUD_ACCESS_KEY`, `NCLOUD_SECRET_KEY` and `NCLOUD_ZONE` adds an `env-<zone>` profile.
- `NCP_MCP_CONFIG` changes the location of the config file.

## Service endpoints

| Service | Endpoint |
|---|---|
| Default (shared gateway) | `https://ncloud.apigw.<zone>/<service>/v2/<action>` |
| `billing` | `https://billingapi.apigw.<zone>/billing/v1` |
| `subaccount` | `https://subaccount.apigw.<zone>/api/v1` |
| `sts` | `https://sts.apigw.<zone>/api/v1` |
| `vnks`, `vses`, `vcdss`, `source*`, etc. | See `SPECIAL` in `src/ncp_mcp/endpoints.py` |

`<zone>` is `ntruss.com` (public), `gov-ntruss.com` (government) or `fin-ntruss.com` (financial).

## Development

```sh
uv sync
uv run pytest
```

See [CONTRIBUTING.md](https://github.com/revert12/ncloud-mcp-server/blob/main/CONTRIBUTING.md) for setup, the pre-commit secret scan, and how the read-only guard is verified.

## License

MIT
