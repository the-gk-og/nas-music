"""AutoEq proxy: index/profile parsing, search, on-demand fetch + cache, API (no real network)."""
import os

import httpx
import pytest

from homebeat.app import autoeq

INDEX = """# Index
This is a list of all equalization profiles.

- [1MORE Aero (ANC Off)](./HypetheSonics/GRAS%20RA0045%20in-ear/1MORE%20Aero%20(ANC%20Off)) by HypetheSonics on GRAS RA0045
- [Sennheiser HD 650](./oratory1990/over-ear/Sennheiser%20HD%20650) by oratory1990
- [Sennheiser HD 650](./crinacle/GRAS%2043AG-7%20over-ear/Sennheiser%20HD%20650) by crinacle on GRAS 43AG-7
- [Sennheiser HD 600](./oratory1990/over-ear/Sennheiser%20HD%20600) by oratory1990
- [Bad](./../etc/passwd) by nobody
"""

HD650 = """Preamp: -6.1 dB
Filter 1: ON LSC Fc 105 Hz Gain 6.4 dB Q 0.70
Filter 2: ON PK Fc 8800 Hz Gain 5.1 dB Q 1.42
Filter 3: OFF PK Fc 118 Hz Gain -3.1 dB Q 0.50
Filter 6: ON HSC Fc 10000 Hz Gain -2.1 dB Q 0.70
"""


def test_parse_index():
    es = autoeq.parse_index(INDEX)
    assert len(es) == 4  # ../ path dropped
    aero = es[0]
    assert aero.name == "1MORE Aero (ANC Off)"
    assert aero.path == "HypetheSonics/GRAS RA0045 in-ear/1MORE Aero (ANC Off)"
    assert (aero.source, aero.rig, aero.form) == ("HypetheSonics", "GRAS RA0045", "in-ear")
    assert es[1].as_dict() == {"id": "oratory1990/over-ear/Sennheiser HD 650", "name": "Sennheiser HD 650",
                               "source": "oratory1990", "rig": None, "form": "over-ear"}


def test_parse_parametric():
    p = autoeq.parse_parametric(HD650)
    assert p["preampDb"] == -6.1
    assert p["filters"] == [
        {"type": "LSC", "fc": 105.0, "gainDb": 6.4, "q": 0.7},
        {"type": "PK", "fc": 8800.0, "gainDb": 5.1, "q": 1.42},
        {"type": "HSC", "fc": 10000.0, "gainDb": -2.1, "q": 0.7},
    ]
    with pytest.raises(autoeq.AutoEqError):
        autoeq.parse_parametric("<html>404</html>")


def test_search_ranking():
    es = autoeq.parse_index(INDEX)
    hits = autoeq.search(es, "hd 650")
    assert [h.source for h in hits] == ["oratory1990", "crinacle"]
    assert [h.name for h in autoeq.search(es, "sennheiser")][:1] == ["Sennheiser HD 600"]
    assert autoeq.search(es, "aero anc") == [es[0]]
    assert autoeq.search(es, "") == es


class Upstream:
    def __init__(self):
        self.calls = []
        self.down = False

    def __call__(self, req: httpx.Request):
        self.calls.append(req.url.path)
        if self.down:
            return httpx.Response(503)
        if req.url.path.endswith("/results/INDEX.md"):
            return httpx.Response(200, text=INDEX)
        if req.url.raw_path == b"/jaakkopasanen/AutoEq/master/results/oratory1990/over-ear/Sennheiser%20HD%20650/" \
                               b"Sennheiser%20HD%20650%20ParametricEQ.txt":
            return httpx.Response(200, text=HD650)
        return httpx.Response(404)


def test_fetch_and_cache(tmp_path):
    u = Upstream()
    a = autoeq.AutoEq(tmp_path, httpx.MockTransport(u))
    assert len(a.entries()) == 4
    prof = a.profile("oratory1990/over-ear/Sennheiser HD 650")
    assert prof["name"] == "Sennheiser HD 650" and len(prof["filters"]) == 3
    n = len(u.calls)
    a.profile("oratory1990/over-ear/Sennheiser HD 650")  # cached on disk
    assert len(u.calls) == n
    with pytest.raises(KeyError):
        a.profile("../../etc/passwd")
    # Stale index + GitHub down: the cached copy keeps working.
    os.utime(tmp_path / "INDEX.md", (1, 1))
    u.down = True
    assert len(autoeq.AutoEq(tmp_path, httpx.MockTransport(u)).entries()) == 4
    # No cache and down: error.
    with pytest.raises(autoeq.AutoEqError):
        autoeq.AutoEq(tmp_path / "empty", httpx.MockTransport(u)).entries()


def test_api(app, client):
    u = Upstream()
    app.state.ctx.http_transport = httpx.MockTransport(u)
    r = client.get("/api/v1/eq/autoeq", params={"q": "hd 650"}).json()
    assert r["total"] == 4 and [x["source"] for x in r["results"]] == ["oratory1990", "crinacle"]
    p = client.get("/api/v1/eq/autoeq/profile", params={"id": r["results"][0]["id"]}).json()
    assert p["preampDb"] == -6.1 and p["filters"][0]["type"] == "LSC"
    assert client.get("/api/v1/eq/autoeq/profile", params={"id": "nope"}).status_code == 404
    # The crinacle profile isn't served by the fake upstream -> 502, not a crash.
    assert client.get("/api/v1/eq/autoeq/profile", params={"id": r["results"][1]["id"]}).status_code == 502


def test_api_upstream_down(app, client):
    u = Upstream()
    u.down = True
    app.state.ctx.http_transport = httpx.MockTransport(u)
    assert client.get("/api/v1/eq/autoeq").status_code == 502
