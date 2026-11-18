"""Pairing QR payload (docs/pairing.md)."""
from __future__ import annotations

import base64
import io
import json

import qrcode

from nasmusic.config import Config


def payload(cfg: Config, server_id: str, cert_sha256: str | None, pair_code: str) -> dict:
    return {
        "v": 1,
        "name": cfg.server_name,
        "serverId": server_id,
        "lanUrl": cfg.lan_url,
        "tunnelUrl": cfg.tunnel_url,
        "certSha256": cert_sha256,
        "pairCode": pair_code,
        "cfAccessClientId": cfg.cf_access_client_id,
        "cfAccessClientSecret": cfg.cf_access_client_secret,
    }


def payload_json(p: dict) -> str:
    return json.dumps(p, separators=(",", ":"), ensure_ascii=False)


def _qr(data: str) -> qrcode.QRCode:
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4, box_size=8)
    q.add_data(data.encode("utf-8"))
    q.make(fit=True)
    return q


def png_data_uri(data: str) -> str:
    img = _qr(data).make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def terminal(data: str) -> str:
    buf = io.StringIO()
    _qr(data).print_ascii(out=buf, invert=True)
    return buf.getvalue()
