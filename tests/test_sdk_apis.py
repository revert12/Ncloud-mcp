"""Check the read/write guard against every API in the official NCP SDK.

tests/data/ncp_sdk_apis.txt is a snapshot of github.com/NaverCloudPlatform/ncloud-sdk-go-v2
(service, HTTP method the SDK uses, path). Refresh it with scripts/refresh_sdk_apis.sh.
When a refresh makes these tests fail, decide per API whether it is a read, a state change
or a credential, then update guard.py or the explicit sets below.
"""

from pathlib import Path

import pytest

from ncp_mcp import endpoints, guard

DATA = Path(__file__).parent / "data" / "ncp_sdk_apis.txt"
ROWS = [
    tuple(line.split())
    for line in DATA.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
SHARED = set(endpoints._SHARED_V2["pub"])

# REST services that change state over GET. ncp_call must block every one of these.
STATE_CHANGING_GETS = {
    ("vcdss", "/cluster/disableBrokerNodePublicEndpoint/{serviceGroupInstanceNo}"),
    ("vcdss", "/cluster/disablePublicDomain/{serviceGroupInstanceNo}"),
    ("vcdss", "/cluster/enablePublicDomain/{serviceGroupInstanceNo}"),
    ("vcdss", "/cluster/restartAllServices/{serviceGroupInstanceNo}"),
    ("vcdss", "/cluster/restartCMAKService/{serviceGroupInstanceNo}"),
    ("vcdss", "/cluster/restartKafkaService/{serviceGroupInstanceNo}"),
    ("vses", "/cluster/restartElasticsearchCluster/{serviceGroupInstanceNo}"),
    ("vses", "/import/stopDataImportJob/{serviceGroupInstanceNo}"),
    ("vses", "/snapshot/releaseSnapshotScheduling/{serviceGroupInstanceNo}"),
    ("vses2", "/cluster/restartCluster/{service-group-no}"),
}

# Reads that ncp_call blocks on purpose (callable through ncp_write + allowlist).
BLOCKED_READS = {
    # 'download' is not a read verb: it would also open shared-gateway downloadDmsFile.
    ("vcdss", "/cluster/downloadCertificate/{serviceGroupInstanceNo}"),
}

# Reads that return credentials.
SENSITIVE = {
    ("server", "/getRootPassword"),
    ("server", "/getRootPasswordServerInstanceList"),
    ("vserver", "/getRootPassword"),
    ("vserver", "/getRootPasswordServerInstanceList"),
    ("vnks", "/clusters/{uuid}/kubeconfig"),
    ("vnks", "/clusters/{uuid}/kubeconfig/reset"),  # PATCH: a write anyway
}


def test_snapshot_loaded():
    assert len(ROWS) > 600
    assert all(len(r) == 3 for r in ROWS)


def test_every_sdk_service_is_allowlisted():
    missing = {svc for svc, _, _ in ROWS} - set(endpoints.allowed_services("pub"))
    assert not missing, f"SDK services missing from endpoints.py: {sorted(missing)}"


def test_explicit_sets_still_exist_in_snapshot():
    known = {(svc, path) for svc, _, path in ROWS}
    stale = (STATE_CHANGING_GETS | BLOCKED_READS | SENSITIVE) - known
    assert not stale, f"no longer in the SDK, remove from this test: {sorted(stale)}"


def _classify(svc, method, path):
    shared = svc in SHARED
    # ncp_call always sends GET; the shared gateway treats GET and POST the same.
    sent = "GET" if shared else method.upper()
    return guard.is_read_only(sent, path, shared)


@pytest.mark.parametrize("svc,method,path", [r for r in ROWS if r[0] in SHARED])
def test_shared_gateway_action(svc, method, path):
    is_read = path.lstrip("/").startswith("get")
    assert _classify(svc, method, path) is is_read
    assert guard.is_read_only("POST", path, shared=True) is is_read


@pytest.mark.parametrize("svc,method,path", [r for r in ROWS if r[0] not in SHARED])
def test_rest_action(svc, method, path):
    if method != "Get":
        expected = False
    else:
        expected = (svc, path) not in STATE_CHANGING_GETS | BLOCKED_READS
    assert _classify(svc, method, path) is expected


def test_sensitive_set_matches_guard():
    flagged = {(svc, path) for svc, _, path in ROWS if guard.is_sensitive(path)}
    assert flagged == SENSITIVE
