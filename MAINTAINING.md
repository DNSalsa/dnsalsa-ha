# Maintaining the DNSalsa app

This folder is published on its own as a public git repository (Home Assistant needs `repository.yaml` at the root). Home Assistant builds the app on the user's device from the `Dockerfile`; there is no image registry to maintain.

- **New version:** raise `version` in `dnsalsa/config.yaml` and add a line to `CHANGELOG.md`. Home Assistant offers the update to users.
- **New cloudflared:** automatic. Every Monday `.github/workflows/cloudflared.yml` checks for a new release and opens a pull request with the new version, both SHA-256 checksums (checked against Cloudflare's release notes) and a new app version. Merge it to release. Run it by hand under *Actions → cloudflared update → Run workflow*.
- **Base image:** pinned in `build.yaml`; raise it now and then.
- **Tests:** `python3 -m unittest discover -s tests` (standard library only). GitHub Actions runs them on every push and pull request, and builds the image for amd64 to check that cloudflared downloads and matches its checksum. The cloudflared workflow does the same build before it opens its pull request.
- **Test without Home Assistant:** `DNSALSA_DATA=/tmp/d DNSALSA_HA_URL=http://127.0.0.1:8123 python3 dnsalsa/rootfs/app/main.py`, with `{"server":"https://staging.dnsalsa.nl"}` in `/tmp/d/options.json`, and open http://127.0.0.1:8099.
