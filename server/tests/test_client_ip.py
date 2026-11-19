from types import SimpleNamespace

from nasmusic.app.context import client_ip


def _req(port, headers, peer="172.18.0.5"):
    ctx = SimpleNamespace(cfg=SimpleNamespace(http_port=8080))
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(ctx=ctx)),
        scope={"server": ("0.0.0.0", port)},
        headers={k.lower(): v for k, v in headers.items()},
        client=SimpleNamespace(host=peer),
    )


def test_relay_caddy_forwarded_for_on_internal_port():
    assert client_ip(_req(8080, {"X-Forwarded-For": "203.0.113.7"})) == "203.0.113.7"
    # last hop wins if a chain is ever present
    assert client_ip(_req(8080, {"X-Forwarded-For": "1.1.1.1, 203.0.113.7"})) == "203.0.113.7"


def test_cloudflare_header_still_preferred():
    assert client_ip(_req(8080, {"CF-Connecting-IP": "198.51.100.2", "X-Forwarded-For": "203.0.113.7"})) == "198.51.100.2"


def test_headers_ignored_on_public_lan_port():
    # 8443 is reachable by anyone on the LAN: never trust client-supplied headers there
    assert client_ip(_req(8443, {"X-Forwarded-For": "6.6.6.6", "CF-Connecting-IP": "6.6.6.6"}, peer="192.168.68.74")) == "192.168.68.74"


def test_no_header_uses_peer():
    assert client_ip(_req(8080, {}, peer="127.0.0.1")) == "127.0.0.1"
