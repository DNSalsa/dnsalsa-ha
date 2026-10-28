# DNSalsa for Home Assistant

Reach your Home Assistant from anywhere at your own dnsalsa.nl address. No port forwarding, and your home IP stays hidden. You need a [DNSalsa](https://dnsalsa.nl) account with the Remote subscription.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDNSalsa%2Fdnsalsa-ha)

Or add `https://github.com/DNSalsa/dnsalsa-ha` yourself under **Settings → Apps → App store → ⋮ → Repositories**.

| App | What it does |
|---|---|
| [DNSalsa](dnsalsa/) | Your Home Assistant at your own dnsalsa.nl address, through a Cloudflare Tunnel |

## For the maintainer

This folder is published on its own as a public git repository (Home Assistant needs `repository.yaml` at the root). Home Assistant builds the app on the user's device from the `Dockerfile`; there is no image registry to maintain.

- **New version:** raise `version` in `dnsalsa/config.yaml` and add a line to `CHANGELOG.md`. Home Assistant offers the update to users.
- **New cloudflared:** automatic. Every Monday `.github/workflows/cloudflared.yml` checks for a new release and opens a pull request with the new version, both SHA-256 checksums (checked against Cloudflare's release notes) and a new app version. Merge it to release. Run it by hand under *Actions → cloudflared update → Run workflow*.
- **Base image:** pinned in `build.yaml`; raise it now and then.
- **Test without Home Assistant:** `DNSALSA_DATA=/tmp/d DNSALSA_HA_URL=http://127.0.0.1:8123 python3 dnsalsa/rootfs/app/main.py`, with `{"server":"https://staging.dnsalsa.nl"}` in `/tmp/d/options.json`, and open http://127.0.0.1:8099.

## Fonts

The sidebar page uses Space Grotesk and Space Mono (`dnsalsa/rootfs/app/fonts/`), both under the SIL Open Font License 1.1.
