# Pairing QR payload

The web UI (`/admin/pair`, password-protected) shows a QR code containing this JSON (UTF-8, compact):

```json
{"v":1,"name":"Home NAS","serverId":"…","lanUrl":"https://192.168.1.50:8443","tunnelUrl":"https://music.example.com",
 "certSha256":"AB:CD:…","pairCode":"8-char code","cfAccessClientId":"…","cfAccessClientSecret":"…"}
```

- `tunnelUrl`, `cfAccessClientId`, `cfAccessClientSecret` may be null (no tunnel configured).
- `certSha256`: SHA-256 fingerprint of the server's self-signed LAN certificate (DER), colon-separated hex.
  The app pins it for `lanUrl` only; `tunnelUrl` uses normal public CA validation.
- The app scans it, calls `POST /api/v1/pair` with `pairCode`, and stores the returned device token
  encrypted with an Android Keystore key (e.g. via Tink, Apache-2.0).
- Pair codes expire after 10 minutes and are single use.
