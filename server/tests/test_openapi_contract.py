"""api/openapi.yaml is THE contract (CLAUDE.md). Every documented operation must be served under /api/v1, and
every /api/v1 route the server serves must be documented, so neither side can drift without this failing."""
import re
from pathlib import Path

import pytest
import yaml

SPEC_PATH = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete"}
PREFIX = "/api/v1"


def _norm(path: str) -> str:
    """'/tracks/{id}/stream' and '/tracks/{track_id}/stream' are the same operation; so is a :path converter."""
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.fixture(scope="module")
def spec():
    if not SPEC_PATH.exists():
        pytest.skip("api/openapi.yaml is not next to the server (not a repo checkout)")
    return yaml.safe_load(SPEC_PATH.read_text())


def _is_websocket(op: dict) -> bool:
    """WebSocket endpoints (/connect, /jam/ws) are documented as a GET upgrade; FastAPI doesn't list them."""
    return "websocket" in (op.get("summary", "") + op.get("description", "")).lower()


def _documented(spec, websockets: bool = False) -> set[tuple[str, str]]:
    return {(m.upper(), _norm(p)) for p, ops in spec["paths"].items() for m, op in ops.items()
            if m in METHODS and _is_websocket(op) == websockets}


def _served(app) -> set[tuple[str, str]]:
    """FastAPI's own view of the routes (app.openapi() works even with openapi_url=None), so this doesn't
    depend on how a FastAPI version stores included routers internally."""
    paths = app.openapi()["paths"]
    return {(m.upper(), _norm(p[len(PREFIX):])) for p, ops in paths.items() if p.startswith(PREFIX + "/")
            for m in ops if m in METHODS}


def _fmt(ops) -> str:
    return "\n".join(f"  {m} {p}" for m, p in sorted(ops, key=lambda x: (x[1], x[0])))


def test_spec_is_valid_openapi_31(spec):
    assert spec["openapi"].startswith("3.1")
    assert spec["servers"][0]["url"] == PREFIX
    for path, ops in spec["paths"].items():
        assert path.startswith("/"), path
        for m, op in ops.items():
            if m in METHODS:
                assert "responses" in op, f"{m.upper()} {path} has no responses"


def test_every_documented_operation_is_served(spec, app):
    missing = _documented(spec) - _served(app)
    assert not missing, "in api/openapi.yaml but not served by the server:\n" + _fmt(missing)


def test_every_served_api_route_is_documented(spec, app):
    undocumented = _served(app) - _documented(spec)
    assert not undocumented, "served under /api/v1 but missing from api/openapi.yaml:\n" + _fmt(undocumented)


def test_documented_websockets_are_served(spec, app):
    """The WebSocket paths in the spec reach their endpoint: an unauthenticated connect is closed with the
    app's 4401, whereas an unrouted websocket path is closed with a plain 1000."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    ws_paths = {p for _, p in _documented(spec, websockets=True)}
    assert {"/connect", "/jam/ws"} <= ws_paths
    client = TestClient(app)
    for path in sorted(ws_paths):
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(PREFIX + path) as ws:
                ws.receive_text()
        assert e.value.code == 4401, f"websocket {path}: closed with {e.value.code}, not served?"


def test_component_refs_resolve(spec):
    """Every $ref points at a schema/response/parameter that exists."""
    refs = set(re.findall(r"\$ref:\s*['\"]?#/components/(\w+)/([\w.-]+)", SPEC_PATH.read_text()))
    comps = spec.get("components", {})
    broken = sorted(f"{kind}/{name}" for kind, name in refs if name not in comps.get(kind, {}))
    assert not broken, "dangling $ref: " + ", ".join(broken)
