# ncp-mcp

NAVER Cloud Platform(Ncloud)용 가벼운 MCP 서버입니다.
*A lightweight MCP server for NAVER Cloud Platform: a few generic tools instead of one tool per API.*

API마다 도구를 하나씩 만들지 않고, **서명된 범용 호출 도구 몇 개**로 모든 Ncloud API를 부릅니다.
도구 정의가 작아서 MCP 연결만으로 대화 컨텍스트를 거의 차지하지 않습니다.

> 네이버클라우드 공식 프로젝트가 아닙니다.

## 도구

| 도구 | 설명 |
|---|---|
| `ncp_profiles` | 사용 가능한 자격증명 프로필·존·기본 프로필·쓰기 허용 여부 (비밀값은 반환하지 않음) |
| `ncp_call` | API 1건 호출. 서명, `responseFormatType=json`, 목록 파라미터 펼치기, 페이지 자동 넘김 |
| `ncp_batch` | 여러 호출을 동시에 실행(최대 50건, 동시 5건). 항목별 실패는 다른 항목에 영향 없음 |

```jsonc
// 서버 목록
ncp_call(service="vserver", action="getServerInstanceList", params={"regionCode": "KR"})

// 서브계정 목록 (REST 형식 서비스)
ncp_call(service="subaccount", action="/sub-accounts")

// 여러 리전 한 번에
ncp_batch(calls=[
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "KR"}},
  {"service": "vserver", "action": "getServerInstanceList", "params": {"regionCode": "JPN"}}
])
```

## 안전장치

- **기본은 읽기 전용입니다.** `get*`·`list*`·`describe*` 같은 조회 동작과 REST 형식의 GET만 허용합니다.
  Ncloud의 공용 게이트웨이 API는 삭제 동작도 GET으로 받기 때문에 HTTP 메서드가 아니라 **동작 이름**으로 판정합니다.
  쓰기를 허용하려면 서버를 `NCP_ALLOW_WRITE=true`로 실행하세요.
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
      "args": ["--from", "git+https://github.com/<owner>/ncp-mcp@v0.1.0", "ncp-mcp"]
    }
  }
}
```

태그로 버전을 고정해 쓰는 것을 권장합니다.

## 자격증명

Ncloud CLI와 같은 파일 `~/.ncloud/configure`(INI)를 읽습니다. 섹션 하나가 프로필 하나입니다.

```ini
[DEFAULT]
ncloud_access_key_id = <access key>
ncloud_secret_access_key = <secret key>
ncloud_api_url = https://ncloud.apigw.ntruss.com

[gov]
ncloud_access_key_id = <gov access key>
ncloud_secret_access_key = <gov secret key>
ncloud_zone = gov
```

- `ncloud_zone`: `pub`(기본) · `gov` · `fin`. 없으면 `ncloud_api_url`의 도메인으로 추정합니다.
- 환경변수 `NCLOUD_ACCESS_KEY`·`NCLOUD_SECRET_KEY`(+ `NCLOUD_ZONE`)를 주면 `env` 프로필이 추가됩니다.
- `NCP_MCP_CONFIG`로 설정 파일 위치를, `NCP_MCP_DEFAULT_PROFILE`로 기본 프로필을 바꿀 수 있습니다.

## 서비스 주소

| 서비스 | 주소 |
|---|---|
| 기본(공용 게이트웨이) | `https://ncloud.apigw.<zone>/<service>/v2/<action>` |
| `billing` | `https://billingapi.apigw.<zone>/billing/v1` |
| `subaccount` | `https://subaccount.apigw.<zone>/api/v1` |
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
