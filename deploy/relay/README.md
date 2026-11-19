# Relay through a friend's server (replaces the Cloudflare tunnel for music)

Cloudflare's terms don't allow streaming media through its tunnel, so the public address
`https://music.whoactuallyneeds.men` goes through a friend's server instead:

```
phone ──HTTPS──▶ friend's server :443 ─▶ friend's server (frps) ══tunnel (NAS dialled out, :2333)══▶ NAS: frpc ─▶ Caddy (HTTPS, Let's Encrypt) ─▶ nasmusic
```

- **Your home router: nothing to forward.** The NAS connects out.
- **Friend's router:** none needed (it's a DMZ). The relay uses the server's public **443** and **2333**;
  port 80 stays with the friend's nginx, so Caddy proves the domain over 443 (TLS-ALPN) only.
- HTTPS is end-to-end: only Caddy on your NAS can decrypt. The friend's server just passes bytes.
- Real visitor IPs reach the server (PROXY protocol), so login rate-limiting works per person.

## 1. Friend's side
Send your friend the filled-in `private/friend-frps.compose.yaml` (it contains the shared token —
send it privately, e.g. a DM, not a public channel). They paste it as a new stack in
Portainer/Dockhand and deploy. Also ask for their **public address** (DDNS name or public IP).

## 2. DNS (Cloudflare)
- Cloudflare Zero Trust → your tunnel → **remove** the `music.whoactuallyneeds.men` public hostname.
- DNS → `music` record: **CNAME** to the friend's DDNS name, or **A** record to their IP.
  **Proxy status: DNS only (grey cloud)** — orange would put Cloudflare back in the middle.

## 3. NAS side
1. Put `frpc.toml` (with FRIEND_ADDRESS + FRP_TOKEN filled) and `Caddyfile` into the nasmusic data
   volume at `/data/relay/` (the coordinator does this through the Dockhand shell).
2. Add `nas-services.yaml`'s two services and two volumes to the nasmusic stack; Save & redeploy.
3. Check: `https://music.whoactuallyneeds.men/api/v1/health` shows a valid certificate and
   `{"status":"ok"}`. Caddy's log shows "certificate obtained successfully".

## Troubleshooting
- Certificate fails: the friend's ISP may block inbound 80/443. Switch to Cloudflare DNS-01 (needs a
  Cloudflare API token scoped to DNS edit) and a different public port such as 8443.
- frpc can't connect: port 2333 not forwarded, firewall on the friend's server, or wrong address/token.
- Rolling back: put the `music` hostname back on the Cloudflare tunnel and remove the two services.
