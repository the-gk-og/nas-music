import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from homebeat import auth
from homebeat.auth import tls


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]


def test_health_is_public(anon):
    r = anon.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and len(body["serverId"]) == 32 and body["version"]
    # serverId is stable
    assert anon.get("/api/v1/health").json()["serverId"] == body["serverId"]


@pytest.mark.parametrize("path", ["/api/v1/library/artists", "/api/v1/favourites", "/api/v1/sync/manifest", "/api/v1/sync/smart",
                                  "/api/v1/mixes/today", "/api/v1/suggestions", "/api/v1/stream/x"])
def test_endpoints_need_token(anon, path):
    assert anon.get(path).status_code == 401
    assert anon.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_pair_flow_single_use(anon, conn):
    code, _ = auth.create_pair_code(conn, _admin_id(conn))
    r = anon.post("/api/v1/pair", json={"pairCode": code.lower(), "deviceName": "Pixel"})
    assert r.status_code == 200
    token = r.json()["deviceToken"]
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (r.json()["deviceId"],)).fetchone()
    assert row["name"] == "Pixel"
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in str(dict(row))  # only the hash is stored

    # Second use of the same code fails.
    assert anon.post("/api/v1/pair", json={"pairCode": code, "deviceName": "Other"}).status_code == 403
    assert anon.post("/api/v1/pair", json={"pairCode": "WRONG123", "deviceName": "x"}).status_code == 403

    ok = anon.get("/api/v1/library/artists", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert conn.execute("SELECT last_seen FROM devices").fetchone()[0] is not None


def test_pair_code_expires(anon, conn):
    code, expires = auth.create_pair_code(conn, _admin_id(conn), now=datetime.now(UTC) - timedelta(minutes=11))
    assert anon.post("/api/v1/pair", json={"pairCode": code, "deviceName": "x"}).status_code == 403


def test_revoke(client, conn):
    assert client.get("/api/v1/favourites").status_code == 200
    assert auth.revoke_device(conn, client.device_id)
    assert client.get("/api/v1/favourites").status_code == 401


def test_password_hashing():
    h = auth.hash_password("correct horse")
    assert h.startswith("scrypt$")
    assert auth.verify_password("correct horse", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("x", None)
    assert not auth.verify_password("x", "garbage")


def test_cert_generated_with_san(cfg):
    from cryptography import x509

    files, created = tls.ensure_cert(cfg.data_dir, cfg.lan_host)
    assert created
    assert tls.ensure_cert(cfg.data_dir, cfg.lan_host)[1] is False  # generated only once
    assert oct(files.key.stat().st_mode & 0o777) == "0o600"
    cert = x509.load_pem_x509_certificate(files.cert.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(ip) for ip in san.get_values_for_type(x509.IPAddress)] == ["192.168.1.50"]
    fp = tls.fingerprint(files.cert)
    assert len(fp.split(":")) == 32 and fp == fp.upper()

