# Maintaining the DNSalsa app

This folder is published on its own as a public git repository (Home Assistant needs `repository.yaml` at the root). Home Assistant builds the app on the user's device from the `Dockerfile`; there is no image registry to maintain.

- **New version:** raise `version` in `dnsalsa/config.yaml` and add a line to `CHANGELOG.md`. Home Assistant offers the update to users.
- **Test before a release:** work on the `dev` branch and add `https://github.com/DNSalsa/dnsalsa-ha#dev` as a repository in a test Home Assistant; it builds the app from that branch. Merging to `main` is the release.
- **Dynamic DNS needs the server:** app 1.3.0 uses `/api/ip`, `ipv4=none` / `ipv6=none` and pairing with `mode: "ddns"`. Deploy the DNSalsa server with those before releasing the app.
- **New cloudflared:** automatic. Every Monday `.github/workflows/cloudflared.yml` checks for a new release and opens a pull request with the new version, both SHA-256 checksums (checked against Cloudflare's release notes) and a new app version. Merge it to release. Run it by hand under *Actions → cloudflared update → Run workflow*.
- **Base image:** pinned in `build.yaml`; raise it now and then.
- **Tests:** `python3 -m unittest discover -s tests` (standard library only). GitHub Actions runs them on every push and pull request, and builds the image for amd64 to check that cloudflared downloads and matches its checksum. The cloudflared workflow does the same build before it opens its pull request.
- **Test without Home Assistant:** `DNSALSA_DATA=/tmp/d DNSALSA_HA_URL=http://127.0.0.1:8123 python3 dnsalsa/rootfs/app/main.py`, with `{"server":"https://staging.dnsalsa.nl"}` in `/tmp/d/options.json`, and open http://127.0.0.1:8099.
