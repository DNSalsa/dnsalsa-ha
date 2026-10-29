# DNSalsa for Home Assistant

Reach your Home Assistant from anywhere at your own dnsalsa.nl address. No port forwarding, and your home IP stays hidden. You need a [DNSalsa](https://dnsalsa.nl) account with the Remote subscription.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDNSalsa%2Fdnsalsa-ha)

Or add `https://github.com/DNSalsa/dnsalsa-ha` yourself under **Settings → Apps → App store → ⋮ → Repositories**.

| App | What it does |
|---|---|
| [DNSalsa](dnsalsa/) | Your Home Assistant at your own dnsalsa.nl address, through a Cloudflare Tunnel |

## Docker (Home Assistant Container or Core)

No apps in your Home Assistant? Run DNSalsa as a container next to it: `ghcr.io/dnsalsa/dnsalsa-ha` (amd64 and arm64).

1. Save [`docker/compose.yaml`](docker/compose.yaml) in an empty folder.
2. Home Assistant on another machine? Change `homeassistant:host-gateway` to its IP (for example `homeassistant:192.168.1.20`) and `DNSALSA_TRUSTED_PROXY` to the IP of the machine running this container.
3. `docker compose up -d`
4. Open http://127.0.0.1:8099 on that machine, or run `docker logs dnsalsa`, for the pairing code. Enter it in your DNSalsa dashboard: hostname → **Setup → Home Assistant**.
5. The page shows the reverse proxy setting Home Assistant needs, with the address to trust. In `configuration.yaml` that is:
   ```yaml
   http:
     use_x_forwarded_for: true
     trusted_proxies:
       - 172.30.57.0/24
   ```

| Tag | What you get |
|---|---|
| `latest` | Every new version (default in the compose file) |
| `1.0` | New 1.0.x versions only |
| `1.0.6` | Exactly this version |

The versions are the same as the Home Assistant app's; see [`dnsalsa/CHANGELOG.md`](dnsalsa/CHANGELOG.md). Update with `docker compose pull && docker compose up -d`. Build it yourself instead: `docker build -t dnsalsa -f docker/Dockerfile https://github.com/DNSalsa/dnsalsa-ha.git#main`.

## For the maintainer

This folder is published on its own as a public git repository (Home Assistant needs `repository.yaml` at the root). Home Assistant builds the app on the user's device from the `Dockerfile`. The standalone container (`docker/Dockerfile`) goes to GHCR.

- **New version:** raise `version` in `dnsalsa/config.yaml` and add a line to `CHANGELOG.md`. Home Assistant offers the update to users, and on the push to `main` `.github/workflows/publish.yml` runs the tests and publishes `ghcr.io/dnsalsa/dnsalsa-ha` with tags `<version>`, `<major.minor>` and `latest`. A push without a new version publishes nothing; *Actions → Publish container → Run workflow* publishes the current version again.
- **GHCR, once:** after the first publish, open the package (organisation *DNSalsa → Packages → dnsalsa-ha → Package settings*) and set *Change visibility* to **Public**, otherwise `docker pull` asks for a login.
- **New cloudflared:** automatic. Every Monday `.github/workflows/cloudflared.yml` checks for a new release and opens a pull request with the new version, both SHA-256 checksums (checked against Cloudflare's release notes) in `dnsalsa/Dockerfile` and `docker/Dockerfile`, and a new app version. Merge it to release. Run it by hand under *Actions → cloudflared update → Run workflow*.
- **Base image:** pinned in `build.yaml`; raise it now and then.
- **Tests:** `python3 -m unittest discover -s tests` (standard library only). GitHub Actions runs them on every push and pull request, and builds both images (app and standalone container) for amd64 to check that cloudflared downloads and matches its checksum. The cloudflared workflow does the same build before it opens its pull request.
- **Test without Home Assistant:** `DNSALSA_DATA=/tmp/d DNSALSA_HA_URL=http://127.0.0.1:8123 python3 dnsalsa/rootfs/app/main.py`, with `{"server":"https://staging.dnsalsa.nl"}` in `/tmp/d/options.json`, and open http://127.0.0.1:8099.

## Fonts

The sidebar page uses Space Grotesk and Space Mono (`dnsalsa/rootfs/app/fonts/`), both under the SIL Open Font License 1.1.
