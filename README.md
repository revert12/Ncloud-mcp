# ncp-mcp

NAVER Cloud Platform(Ncloud)용 가벼운 MCP 서버입니다.
*A lightweight MCP server for NAVER Cloud Platform: a few generic tools instead of one tool per API.*

API마다 도구를 하나씩 만들지 않고, **서명된 범용 호출 도구 몇 개**로 모든 Ncloud API를 부릅니다.
도구 정의가 작아서 MCP 연결만으로 대화 컨텍스트를 거의 차지하지 않습니다.

> 네이버클라우드 공식 프로젝트가 아닙니다.

## 도구

| 도구 | 설명 |
|---|---|
| `ncp_profiles` | 사용 가능한 프로필·존, 규칙에 어긋나 꺼진 프로필과 이유, 쓰기 허용 여부. `verify=true`면 키 주인(계정 ID·로그인 별칭·메인/서브) 확인 (비밀값은 반환하지 않음) |
| `ncp_call` | **조회 전용** API 1건 호출. 서명, `responseFormatType=json`, 목록 파라미터 펼치기, 페이지 자동 넘김, `fields`로 필요한 필드만 반환 |
| `ncp_batch` | 조회 여러 건을 동시에 실행(최대 50건, 동시 5건). 항목별 실패는 다른 항목에 영향 없음 |
| `ncp_write` | 생성·변경·삭제 API 호출. **`NCP_ALLOW_WRITE=true`로 실행했을 때만 등록됨** |

```jsonc
// 서버 목록 (필요한 필드만)
ncp_call(service="vserver", action="getServerInstanceList", params={"regionCode": "KR"},
         fields=["serverName", "serverInstanceStatus.code", "privateIp"])

// 서브계정 목록 (REST 형식 서비스)
ncp_call(service="subaccount", action="/sub-accounts")

// 여러 리전 한 번에
ncp_batch(calls=[
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "KR"}},
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "JPN"}}
])
```

## 안전장치

- **조회와 변경을 도구 단위로 나눴습니다.** `ncp_call`·`ncp_batch`는 `get*`·`list*`·`describe*` 같은 조회 동작과
  REST 형식의 GET만 허용합니다. Ncloud의 공용 게이트웨이 API는 삭제 동작도 GET으로 받기 때문에
  HTTP 메서드가 아니라 **동작 이름**으로 판정합니다.
- 변경은 `ncp_write`로만 할 수 있고, 이 도구는 서버를 `NCP_ALLOW_WRITE=true`로 실행했을 때만 나타납니다.
- 도구마다 MCP 표준 표시(`readOnlyHint`/`destructiveHint`)를 붙였습니다. 클라이언트 권한 설정에서
  **`ncp_call`·`ncp_batch`는 허용, `ncp_write`는 매번 확인**으로 나눠 두는 것을 권장합니다.
  (Claude Code 예: `permissions.allow`에 `mcp__ncp__ncp_call`, `permissions.ask`에 `mcp__ncp__ncp_write`)
- 서명된 요청은 선택한 존의 API 게이트웨이 도메인(`*.apigw.ntruss.com` 등)으로만 보냅니다.
- 키는 오류 메시지와 결과에 포함하지 않습니다. 최종 권한 경계는 Access Key의 서브계정 권한이므로 **조회 권한만 준 서브계정 키** 사용을 권장합니다.

## 설치

Python 3.10 이상, [uv](https://docs.astral.sh/uv/) 필요.

```jsonc
// .mcp.json
{
  "mcpServers": {
    "ncp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/<owner>/ncp-mcp@v0.2.0", "ncp-mcp"]
    }
  }
}
```

태그로 버전을 고정해 쓰는 것을 권장합니다.

## 자격증명

Ncloud CLI와 같은 파일 `~/.ncloud/configure`(INI)를 읽습니다. 섹션 하나가 프로필 하나입니다.

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

**프로필 규칙**(어긴 프로필은 사용하지 않고 `ncp_profiles`의 `disabled`에 이유를 보여 줍니다)
- `ncloud_zone`(`pub` · `gov` · `fin`)을 반드시 적습니다.
- 프로필 이름은 소문자·숫자·하이픈이고 **끝이 존과 같아야** 합니다(`-pub`, `-gov`, `-fin`). 이름만 봐도 존을 알 수 있게 하기 위해서입니다. 그래서 `[DEFAULT]`는 쓸 수 없습니다.
- 프로필이 2개 이상이면 호출할 때마다 `profile`을 지정해야 합니다. 기본 프로필은 없습니다.
- 사용 가능한 프로필 이름은 서버 시작 시 도구 정의에 선택 목록으로 들어갑니다. 프로필을 바꾸면 MCP 서버를 다시 시작하세요.
- `ncp_profiles(verify=true)`는 각 키가 실제로 어느 NCP 계정·사용자의 키인지 STS(`caller-identity`)로 확인해 보여 줍니다.

Ncloud CLI는 `--profile <이름>`으로 같은 프로필을 쓸 수 있습니다.

- 환경변수 `NCLOUD_ACCESS_KEY`·`NCLOUD_SECRET_KEY`·`NCLOUD_ZONE`을 주면 `env-<zone>` 프로필이 추가됩니다.
- `NCP_MCP_CONFIG`로 설정 파일 위치를 바꿀 수 있습니다.

## 서비스 주소

| 서비스 | 주소 |
|---|---|
| 기본(공용 게이트웨이) | `https://ncloud.apigw.<zone>/<service>/v2/<action>` |
| `billing` | `https://billingapi.apigw.<zone>/billing/v1` |
| `subaccount` | `https://subaccount.apigw.<zone>/api/v1` |
| `sts` | `https://sts.apigw.<zone>/api/v1` |
| `vnks`, `vses`, `vcdss`, `source*` 등 | `src/ncp_mcp/endpoints.py`의 `SPECIAL` 참고 |

`<zone>`은 `ntruss.com`(민간) · `gov-ntruss.com`(공공) · `fin-ntruss.com`(금융)입니다.

## 검증 상태

| 항목 | 상태 |
|---|---|
| 민간(pub) 존 조회 | 실계정 확인 |
| 공공(gov)·금융(fin) 존 | 미확인 (주소 규칙만 반영) |
| 페이지 자동 넘김 | 미확인 (한 페이지를 넘는 목록으로 아직 시험 안 함) |
| 쓰기 동작 | 미확인 |

## 개발

```sh
uv sync
uv run pytest
git config core.hooksPath .githooks   # 커밋 전 gitleaks 비밀값 검사
```

## 라이선스

MIT
