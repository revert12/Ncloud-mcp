"""Client behaviour against a mocked NCP gateway (no network)."""

import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from ncp_mcp import client
from ncp_mcp.client import CallError, call
from ncp_mcp.config import Profile

PUB = Profile("test-pub", "pub", "AK", "SK")
SERVERS = [{"serverInstanceNo": str(i), "serverName": f"s{i}"} for i in range(1, 6)]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _nosleep(_):
        return None

    monkeypatch.setattr(client, "_sleep", _nosleep)
    for v in ("NCP_ALLOW_WRITE", "NCP_WRITE_ALLOWLIST", "NCP_MCP_EXTRA_SERVICES"):
        monkeypatch.delenv(v, raising=False)


def run(handler, **kw):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await call(http, PUB, **kw)

    return asyncio.run(go())


def server_list_handler(cap=None, log=None):
    """Mimic getServerInstanceList: no pageNo -> all (or first `cap`) with totalRows=total;
    pageNo -> that page, totalRows = items on the page; pageNo 0 and 1 = first page."""

    def handler(req: httpx.Request):
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}
        if log is not None:
            log.append(q)
        if "pageNo" in q:
            size = int(q["pageSize"])
            start = (max(int(q["pageNo"]), 1) - 1) * size
            items = SERVERS[start:start + size]
            body = {"totalRows": len(items), "serverInstanceList": items}
        else:
            items = SERVERS if cap is None else SERVERS[:cap]
            body = {"totalRows": len(SERVERS), "serverInstanceList": items}
        return httpx.Response(200, json={"getServerInstanceListResponse": body})

    return handler


def items_of(r):
    return r["data"]["getServerInstanceListResponse"]["serverInstanceList"]


# ---- paging -------------------------------------------------------------------------

def test_paging_complete_in_one_response():
    r = run(server_list_handler(), service="vserver", action="getServerInstanceList")
    assert r["paging"]["complete"] is True and r["pages"] == 1
    assert len(items_of(r)) == 5


def test_paging_refetches_when_server_caps_results():
    log = []
    r = run(server_list_handler(cap=2, log=log), service="vserver", action="getServerInstanceList")
    assert r["paging"] == {"auto": True, "complete": True, "pages": 3, "returned": 5, "total": 5}
    assert [s["serverInstanceNo"] for s in items_of(r)] == ["1", "2", "3", "4", "5"]
    assert [q.get("pageNo") for q in log[1:]] == ["1", "2", "3"]   # starts at page 1, no skip
    assert "truncated" not in r


def test_paging_reports_incomplete_at_page_limit(monkeypatch):
    monkeypatch.setattr(client, "MAX_PAGES", 1)
    r = run(server_list_handler(cap=2), service="vserver", action="getServerInstanceList")
    assert r["paging"]["complete"] is False
    assert r["truncated"]["returned"] == 2 and r["truncated"]["total"] == 5


def test_paging_explicit_page_is_marked_partial():
    r = run(server_list_handler(), service="vserver", action="getServerInstanceList",
            params={"pageNo": 2, "pageSize": 2})
    assert r["paging"]["auto"] is False and r["paging"]["complete"] is False
    assert "one page only" in r["paging"]["reason"]


def test_paging_disabled():
    r = run(server_list_handler(cap=2), service="vserver", action="getServerInstanceList",
            paginate=False)
    assert r["paging"]["complete"] is False and r["truncated"]["reason"] == "paginate=false"


def test_rest_paging_follows_has_next():
    def handler(req):
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(req.url)).query).items()}
        page = int(q.get("page", 0))
        size = int(q.get("size", 2))
        chunk = SERVERS[page * size:(page + 1) * size]
        return httpx.Response(200, json={
            "page": page, "items": chunk, "totalItems": len(chunk),
            "hasNext": (page + 1) * size < len(SERVERS)})

    r = run(handler, service="subaccount", action="/sub-accounts")
    assert r["paging"]["complete"] is True and r["paging"]["pages"] == 3
    assert len(r["data"]["items"]) == 5


def test_unknown_list_shape_reports_incomplete():
    def handler(req):
        return httpx.Response(200, json={"contents": SERVERS[:2], "totalElements": 5, "pageNo": 0})

    r = run(handler, service="vserver", action="getRegionList")
    assert r["paging"]["complete"] is False and r["truncated"]["total"] == 5


# ---- retries ------------------------------------------------------------------------

def test_read_retries_on_429_then_succeeds():
    calls = []

    def handler(req):
        calls.append(req.headers["x-ncp-apigw-timestamp"])
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"errorCode": "420"}})
        return httpx.Response(200, json={"getRegionListResponse": {"totalRows": 0, "regionList": []}})

    r = run(handler, service="vserver", action="getRegionList")
    assert r["status"] == 200 and r["attempts"] == 2
    assert all(calls)  # every attempt is signed with its own timestamp header


def test_read_gives_up_after_retries_on_503():
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(503, json={"error": "Endpoint Error"})

    r = run(handler, service="vserver", action="getRegionList")
    assert r["status"] == 503 and len(n) == 1 + client.RETRIES


def test_read_retries_timeout():
    n = []

    def handler(req):
        n.append(1)
        if len(n) == 1:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, json={"getRegionListResponse": {"totalRows": 0, "regionList": []}})

    assert run(handler, service="vserver", action="getRegionList")["status"] == 200


def test_write_is_never_retried(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    monkeypatch.setenv("NCP_WRITE_ALLOWLIST", "vserver:stopServerInstances")
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(503, json={})

    r = run(handler, service="vserver", action="stopServerInstances", mode="write")
    assert r["status"] == 503 and len(n) == 1


def test_write_timeout_reports_unknown_outcome(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    monkeypatch.setenv("NCP_WRITE_ALLOWLIST", "vserver:stopServerInstances")

    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(CallError, match="may or may not have been applied"):
        run(handler, service="vserver", action="stopServerInstances", mode="write")


# ---- allowlists ---------------------------------------------------------------------

def ok_handler(req):
    return httpx.Response(200, json={})


def test_unknown_service_rejected_before_network():
    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match="allowlist"):
        run(handler, service="madeupservice", action="getX")


def test_extra_service_env(monkeypatch):
    monkeypatch.setenv("NCP_MCP_EXTRA_SERVICES", "pub:madeupservice")
    assert run(ok_handler, service="madeupservice", action="getX")["status"] == 200


def test_write_requires_allowlist_entry(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")

    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match="NCP_WRITE_ALLOWLIST"):
        run(handler, service="vserver", action="terminateServerInstances", mode="write")
    monkeypatch.setenv("NCP_WRITE_ALLOWLIST", "vserver:stopServerInstances")
    with pytest.raises(CallError, match="NCP_WRITE_ALLOWLIST"):
        run(handler, service="vserver", action="terminateServerInstances", mode="write")


def test_read_mode_blocks_write_actions():
    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match="write operation"):
        run(handler, service="vserver", action="terminateServerInstances")


@pytest.mark.parametrize(
    "action",
    ["terminateserverinstances", "TerminateServerInstances", "terminateServerInstances/x",
     "getServerInstanceList/terminate"],
)
def test_read_mode_blocks_unproven_shared_actions(action):
    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match="write operation"):
        run(handler, service="vserver", action=action)


@pytest.mark.parametrize(
    "service,action",
    [("vserver", "getRootPassword"), ("vserver", "getRootPasswordServerInstanceList"),
     ("vnks", "/clusters/abc/kubeconfig")],
)
def test_read_mode_blocks_sensitive_actions(service, action):
    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match="credentials"):
        run(handler, service=service, action=action)


def test_sensitive_action_allowed_only_via_write_allowlist(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    with pytest.raises(CallError, match="NCP_WRITE_ALLOWLIST"):
        run(ok_handler, service="vserver", action="getRootPassword", mode="write")
    monkeypatch.setenv("NCP_WRITE_ALLOWLIST", "vserver:getRootPassword")
    assert run(ok_handler, service="vserver", action="getRootPassword", mode="write")["status"] == 200


# ---- input validation ---------------------------------------------------------------

@pytest.mark.parametrize("kw", [
    {"params": ["not", "a", "dict"]},
    {"fields": "serverName"},
    {"params": {"x": object()}},
    {"action": "getX?evil=1"},
    {"action": "/a/../b"},
])
def test_bad_inputs_raise_callerror(kw):
    args = {"service": "vserver", "action": "getRegionList", **kw}
    with pytest.raises(CallError):
        run(ok_handler, **args)


@pytest.mark.parametrize("params", [
    {"pageSize": "abc"}, {"pageSize": 0}, {"pageSize": True}, {"pageSize": 2.5},
    {"pageNo": -1}, {"size": "x"}, {"page": -1},
])
def test_bad_paging_params_rejected_before_network(params):
    def handler(req):
        raise AssertionError("must not send")

    with pytest.raises(CallError, match=f"'{next(iter(params))}' must be an integer"):
        run(handler, service="vserver", action="getServerInstanceList", params=params)


@pytest.mark.parametrize("params", [{"pageNo": 0, "pageSize": 2}, {"pageSize": "2"}, {"page": 0}])
def test_valid_paging_params_accepted(params):
    run(server_list_handler(), service="vserver", action="getServerInstanceList", params=params)


def test_paginate_must_be_bool():
    with pytest.raises(CallError, match="paginate"):
        run(ok_handler, service="vserver", action="getRegionList", paginate="false")


# ---- result size cap ----------------------------------------------------------------

def big_list_handler(n=200):
    def handler(req):
        items = [{"serverInstanceNo": str(i), "memo": "x" * 500} for i in range(n)]
        return httpx.Response(200, json={"getServerInstanceListResponse":
                                         {"totalRows": n, "serverInstanceList": items}})
    return handler


def test_large_list_is_trimmed_to_limit():
    r = run(big_list_handler(), service="vserver", action="getServerInstanceList", max_chars=20_000)
    assert len(json.dumps(r, ensure_ascii=False)) <= 20_000
    kept = len(items_of(r))
    assert 0 < kept < 200
    assert r["truncated"]["returned"] == kept and r["truncated"]["total"] == 200
    assert "fields" in r["truncated"]["reason"]
    assert r["paging"]["complete"] is False


def test_small_result_is_untouched():
    r = run(server_list_handler(), service="vserver", action="getServerInstanceList", max_chars=20_000)
    assert "truncated" not in r and len(items_of(r)) == 5


def test_non_list_result_is_cut_as_text():
    def handler(req):
        return httpx.Response(200, json={"blob": "y" * 50_000})

    r = run(handler, service="vserver", action="getRegionList", max_chars=20_000)
    assert len(json.dumps(r, ensure_ascii=False)) <= 20_000
    assert isinstance(r["data"], str) and "cut JSON text" in r["truncated"]["reason"]


# ---- write allowlist methods --------------------------------------------------------

@pytest.mark.parametrize("allow,method,action,ok", [
    # shared gateway: GET and POST are the same call, either entry form allows both
    ("vserver:stopServerInstances", "GET", "stopServerInstances", True),
    ("vserver:stopServerInstances", "POST", "stopServerInstances", True),
    ("POST vserver:stopServerInstances", "GET", "stopServerInstances", True),
    ("vserver:stopServerInstances", "DELETE", "stopServerInstances", False),
    ("vserver:stopServerInstances", "POST", "startServerInstances", False),
    # REST services: methods are distinct
    ("POST subaccount:/sub-accounts", "POST", "/sub-accounts", True),
    ("POST subaccount:/sub-accounts", "POST", "sub-accounts", True),
    ("post subaccount:sub-accounts", "POST", "/sub-accounts", True),
    ("POST subaccount:/sub-accounts", "DELETE", "/sub-accounts", False),
    ("subaccount:/sub-accounts", "POST", "/sub-accounts", False),
    ("DELETE subaccount:/sub-accounts/1", "DELETE", "/sub-accounts/1", True),
])
def test_write_allowlist_matches_method(monkeypatch, allow, method, action, ok):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    monkeypatch.setenv("NCP_WRITE_ALLOWLIST", allow)
    service = "vserver" if "vserver" in allow else "subaccount"
    if ok:
        assert run(ok_handler, service=service, action=action, method=method, mode="write")["status"] == 200
    else:
        with pytest.raises(CallError, match="NCP_WRITE_ALLOWLIST"):
            run(ok_handler, service=service, action=action, method=method, mode="write")


def test_shared_post_read_goes_to_ncp_call(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    with pytest.raises(CallError, match="use ncp_call"):
        run(ok_handler, service="vserver", action="getServerInstanceList", method="POST", mode="write")


def test_shared_block_message_suggests_entry_without_method(monkeypatch):
    monkeypatch.setenv("NCP_ALLOW_WRITE", "true")
    with pytest.raises(CallError, match="'vserver:/stopServerInstances'"):
        run(ok_handler, service="vserver", action="stopServerInstances", method="POST", mode="write")


# ---- batch isolation (server layer) -------------------------------------------------

def test_batch_isolates_failures(monkeypatch, tmp_path):
    cfg = tmp_path / "configure"
    cfg.write_text("[test-pub]\nncloud_access_key_id = AK\nncloud_secret_access_key = SK\n"
                   "ncloud_zone = pub\n", encoding="utf-8")
    monkeypatch.setenv("NCP_MCP_CONFIG", str(cfg))
    for v in ("NCLOUD_ACCESS_KEY", "NCLOUD_SECRET_KEY", "NCLOUD_ZONE"):
        monkeypatch.delenv(v, raising=False)
    from ncp_mcp import server

    def handler(req):
        if "boom" in str(req.url):
            raise RuntimeError("unexpected")
        return httpx.Response(200, json={"getRegionListResponse": {"totalRows": 0, "regionList": []}})

    monkeypatch.setattr(server, "_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(server.ncp_batch([
        {"service": "vserver", "action": "getRegionList"},
        "not-a-dict",
        {"service": "vserver", "action": "getRegionList", "params": "bad"},
        {"service": "madeupservice", "action": "getX"},
        {"service": "vserver", "action": "getRegionList", "params": {"boom": 1}},
        {"service": "vserver", "action": "getRegionList", "method": "POST"},
        {"service": "vserver", "action": "getRegionList"},
        {"service": "vserver", "action": "getRegionList", "paginate": "false"},
    ]))
    assert out["count"] == 8 and out["failed"] == 6
    assert [r["index"] for r in out["results"]] == list(range(8))
    assert out["results"][0]["status"] == 200 and out["results"][6]["status"] == 200
    assert "internal error (RuntimeError)" in out["results"][4]["error"]
    assert "JSON boolean" in out["results"][7]["error"]
    assert json.dumps(out).find("SK") == -1


def test_batch_reads_config_once(monkeypatch, tmp_path):
    cfg = tmp_path / "configure"
    cfg.write_text("[test-pub]\nncloud_access_key_id = AK\nncloud_secret_access_key = SK\n"
                   "ncloud_zone = pub\n", encoding="utf-8")
    monkeypatch.setenv("NCP_MCP_CONFIG", str(cfg))
    for v in ("NCLOUD_ACCESS_KEY", "NCLOUD_SECRET_KEY", "NCLOUD_ZONE"):
        monkeypatch.delenv(v, raising=False)
    from ncp_mcp import server

    reads = []
    real = server.load_profiles

    def counting():
        reads.append(1)
        return real()

    monkeypatch.setattr(server, "load_profiles", counting)
    monkeypatch.setattr(server, "_http", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"getRegionListResponse": {"totalRows": 0, "regionList": []}})
    )))
    out = asyncio.run(server.ncp_batch(
        [{"service": "vserver", "action": "getRegionList"}] * 10
        + [{"service": "vserver", "action": "getRegionList", "profile": "nope-pub"}]
    ))
    assert len(reads) == 1
    assert out["count"] == 11 and out["failed"] == 1
    assert "unknown profile 'nope-pub'" in out["results"][10]["error"]
