# Changelog

## 1.3.6

- Fixes a blank app page on iPhone and iPad after pairing: the page now redraws itself once its fonts have loaded.

## 1.3.5

- A **Copy** button next to the pairing code.

## 1.3.4

- On the first start, the app turns on **Show in sidebar** and **Watchdog**. Turn either off on the app's page and it stays off.
- Clearer pairing steps: step 2 explains the difference between remote access and dynamic DNS, and which one needs a port forward.

## 1.3.3

- The app page is about 12 KB instead of 60 KB: the fonts load once and stay cached, instead of coming along with every page.

## 1.3.2

- The app page no longer flashes white while it waits for the connection: it updates in place instead of reloading.

## 1.3.1

- cloudflared 2026.10.7.

## 1.3.0

- Dynamic DNS without a tunnel, also on a free DNSalsa account. Pair under **Setup → Dynamic DNS → Home Assistant app** in the dashboard, and the app keeps your hostname on your home address: it looks up your IPv4 and IPv6 address and sends both to DNSalsa in one update.
- On the app page: switches for IPv4 and IPv6, and how often to check (30 seconds to 1 hour, 5 minutes by default). Turning one off removes that record.
- An address the app can't find three times in a row (you lost IPv6, say) gets its record removed, so the name doesn't point at an old address.

## 1.2.1

- The sensors from 1.2.0 are gone again, and the app no longer asks for access to Home Assistant's API.

## 1.1.1

- The IP banning tip has a "Done, hide this" button. Once pressed, it stays hidden, also after pairing again.

## 1.1.0

- Works with Home Assistant on any port. New Home Assistant OS installs (2026.8 and later) listen on port 80 instead of 8123; the app now asks Home Assistant where it listens, and whether it uses its own certificate, and sets the tunnel to match. If that changes later, the app fixes the tunnel by itself.
- "Connected" now means your address really reaches Home Assistant. The app checks it through Cloudflare, like a visitor, and says so when Home Assistant doesn't answer.

## 1.0.5

- The setup steps now suggest turning on IP banning, with a ban after 10 failed logins.

## 1.0.4

- The DNSalsa dashboard shows your Home Assistant version when you enter the pairing code.

## 1.0.3

- The reverse proxy check no longer shows up in Home Assistant as a failed login from 203.0.113.7.
- New look for the DNSalsa page in the sidebar.

## 1.0.2

- Watchdog support: turn on **Watchdog** on the app page and Home Assistant restarts the app if it stops responding.

## 1.0.1

- cloudflared 2026.10.5.

## 1.0.0

- First version: pair with a code from your DNSalsa dashboard, Cloudflare Tunnel to Home Assistant, reverse proxy check.
