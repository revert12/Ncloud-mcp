from ncp_mcp import endpoints, guard
from ncp_mcp.client import _build_path, _find_page, flatten_params, CallError
from ncp_mcp.signer import make_signature

import pytest


def test_signature_is_deterministic():
    a = make_signature("GET", "/vserver/v2/getRegionList?responseFormatType=json", "1700000000000", "AK", "SK")
    b = make_signature("get", "/vserver/v2/getRegionList?responseFormatType=json", "1700000000000", "AK", "SK")
    assert a == b
    assert a != make_signature("GET", "/vserver/v2/getRegionList", "1700000000000", "AK", "SK")


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("GET", "/vserver/v2/getServerInstanceList", True),
        ("GET", "/vserver/v2/terminateServerInstances", False),
        ("GET", "/vserver/v2/stopServerInstances", False),
        ("GET", "/vpc/v2/deleteNatGatewayInstance", False),
        ("GET", "/api/v1/sub-accounts", True),
        ("POST", "/api/v1/sub-accounts", False),
        ("DELETE", "/api/v1/sub-accounts/123", False),
        ("GET", "/billing/v1/cost/getDemandCostList", True),
    ],
)
def test_read_only(method, path, expected):
    assert guard.is_read_only(method, path) is expected


def test_flatten_params():
    got = flatten_params(
        {
            "regionCode": "KR",
            "serverInstanceNoList": ["1", "2"],
            "ruleList": [{"protocolTypeCode": "TCP", "portRange": "22"}],
            "isTrue": True,
            "skip": None,
        }
    )
    assert got == [
        ("regionCode", "KR"),
        ("serverInstanceNoList.1", "1"),
        ("serverInstanceNoList.2", "2"),
        ("ruleList.1.protocolTypeCode", "TCP"),
        ("ruleList.1.portRange", "22"),
        ("isTrue", "true"),
    ]


def test_resolve_zones():
    assert endpoints.resolve("pub", "vserver") == ("ncloud.apigw.ntruss.com", "/vserver/v2")
    assert endpoints.resolve("gov", "vserver") == ("ncloud.apigw.gov-ntruss.com", "/vserver/v2")
    assert endpoints.resolve("pub", "subaccount") == ("subaccount.apigw.ntruss.com", "/api/v1")
    assert endpoints.resolve("pub", "sts") == ("sts.apigw.ntruss.com", "/api/v1")
    assert endpoints.resolve("gov", "subaccount") == ("subaccount.apigw.gov-ntruss.com", "/api/v2")
    assert endpoints.resolve("fin", "billing") == ("billingapi.apigw-pub.fin-ntruss.com", "/billing/v1")
    with pytest.raises(endpoints.NotAllowed):
        endpoints.resolve("gov", "sts")          # not documented for gov
    with pytest.raises(endpoints.NotAllowed):
        endpoints.resolve("pub", "madeupservice")
    assert endpoints.resolve("fin", "vserver")[0] == "fin-ncloud.apigw.fin-ntruss.com"
    with pytest.raises(ValueError):
        endpoints.resolve("pub", "evil.com/x")


def test_build_path_rejects_urls():
    assert _build_path("/vserver/v2", "getRegionList") == "/vserver/v2/getRegionList"
    for bad in ("https://evil.com", "//evil.com", "../x", ""):
        with pytest.raises(CallError):
            _build_path("/vserver/v2", bad)


@pytest.mark.parametrize(
    "name,zone,ok",
    [
        ("mycompany-pub", "pub", True),
        ("customer-a-gov", "gov", True),
        ("bank-fin", "fin", True),
        ("DEFAULT", "pub", False),        # no zone suffix
        ("customer-gov", "pub", False),   # suffix/zone mismatch
        ("customer-pub", "", False),      # zone missing
        ("customer-xyz", "xyz", False),   # invalid zone
        ("Customer-pub", "pub", False),   # uppercase
    ],
)
def test_profile_validation(name, zone, ok):
    from ncp_mcp.config import validate

    assert (validate(name, zone, "AK", "SK") is None) is ok


def test_profile_validation_requires_keys():
    from ncp_mcp.config import validate

    assert validate("a-pub", "pub", "", "SK") is not None


def test_load_profiles_reports_problems(tmp_path, monkeypatch):
    from ncp_mcp.config import load_profiles

    cfg = tmp_path / "configure"
    cfg.write_text(
        "[DEFAULT]\nncloud_access_key_id = AK\nncloud_secret_access_key = SK\n"
        "[good-gov]\nncloud_access_key_id = AK\nncloud_secret_access_key = SK\nncloud_zone = gov\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NCP_MCP_CONFIG", str(cfg))
    for v in ("NCLOUD_ACCESS_KEY", "NCLOUD_SECRET_KEY", "NCLOUD_ZONE"):
        monkeypatch.delenv(v, raising=False)
    res = load_profiles()
    assert list(res.profiles) == ["good-gov"]
    assert [p["name"] for p in res.problems] == ["DEFAULT"]


def test_project_fields():
    from ncp_mcp.client import project

    items = [{"serverName": "a", "status": {"code": "RUN", "codeName": "x"}, "ip": "1"}]
    assert project(items, ["serverName", "status.code", "missing"]) == [
        {"serverName": "a", "status.code": "RUN", "missing": None}
    ]


def test_find_page():
    data = {"getXListResponse": {"totalRows": 3, "returnCode": "0", "xList": [1, 2]}}
    node, key, total = _find_page(data)
    assert key == "xList" and total == 3
