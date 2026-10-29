# DNSalsa for Home Assistant

Reach your Home Assistant from anywhere at your own dnsalsa.nl address. No port forwarding, and your home IP stays hidden. You need a [DNSalsa](https://dnsalsa.nl) account with the Remote subscription.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FDNSalsa%2Fdnsalsa-ha)

Or add `https://github.com/DNSalsa/dnsalsa-ha` yourself under **Settings → Apps → App store → ⋮ → Repositories**.

| App | What it does |
|---|---|
| [DNSalsa](dnsalsa/) | Your Home Assistant at your own dnsalsa.nl address, through a Cloudflare Tunnel |

Home Assistant Container or Core has no apps: use the [DNSalsa connector](https://github.com/DNSalsa/dnsalsa-docker) instead.

## Fonts

The sidebar page uses Space Grotesk and Space Mono (`dnsalsa/rootfs/app/fonts/`), both under the SIL Open Font License 1.1.
