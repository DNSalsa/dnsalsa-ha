# DNSalsa

Reach your Home Assistant from anywhere at your own address, like `myhome.dnsalsa.nl`. No port forwarding, no router settings, and your home IP stays hidden. The traffic runs through a Cloudflare Tunnel that DNSalsa sets up for you.

For that you need a DNSalsa account with the Remote subscription. Without Remote, the app can still keep a hostname on your home address (dynamic DNS), see below.

## Connecting

1. On the app page, turn on **Start on boot**, **Watchdog** and **Show in sidebar**, and start the app. Open **DNSalsa** in the sidebar. It shows a code like `K7QP-4XM2`.
2. In your [DNSalsa dashboard](https://dnsalsa.nl/dashboard), open the hostname you want, choose **Setup → Home Assistant** and enter the code.
3. Within a few seconds this app shows your address and **Connected**. The app finds the port Home Assistant uses (80 on new installs, 8123 on older ones) and whether it uses its own certificate; you don't set anything.

## One setting in Home Assistant

Home Assistant only accepts traffic from the tunnel after you allow it. The app checks this and, when it's needed, shows an **Open network settings** button:

1. **Settings → System → Network**, section **HTTP Server**, part **Reverse proxy**.
2. Turn on **Trust X-Forwarded-For**.
3. Add `172.30.33.0/24` as a trusted proxy (the app has a copy button). Already have other proxies there? Keep them.
4. Recommended, same section: turn on **Enable IP banning**, turn off **Unlimited login attempts** and set **Login attempts before ban** to **10**. Home Assistant then blocks an address after 10 failed logins. Bans don't expire; remove them from `ip_bans.yaml` if you lock yourself out.
5. Save, and confirm the new settings after Home Assistant restarts. Without that confirmation Home Assistant puts the old settings back after 5 minutes.

## Phone app

In the Home Assistant Companion app: **Settings → Companion app → Server → External URL**, and enter your DNSalsa address.

## Dynamic DNS without a tunnel

Free accounts too. The app keeps your hostname on your home address, like a DDNS client. You reach Home Assistant from outside only with a port forward on your router; Remote needs none.

1. Start the app and open **DNSalsa** in the sidebar. It shows a code.
2. In your DNSalsa dashboard, open the hostname, choose **Setup → Dynamic DNS → Home Assistant app** and enter the code. The app gets its own token for that hostname; a token you used before stops working.
3. The app page shows your IPv4 and IPv6 address as DNSalsa has them.

On the app page:

- **IPv4** and **IPv6** switches. Turning one off removes that record at DNSalsa. Free accounts get IPv4 only; the page says so when DNSalsa leaves IPv6 out.
- **Check every**: 30 seconds to 1 hour, 5 minutes by default. A check costs nothing; DNSalsa only changes a record when your address changes.

The app finds your addresses by asking DNSalsa (`/api/ip`), once over IPv4 and once over IPv6. If it can't find an address three times in a row, it removes that record, so your name doesn't point at an old address. No IPv6 address shows up while your network has IPv6? Then the container network of Home Assistant has no IPv6.

## Help

- **Code expired:** the app shows a new one by itself.
- **Use another hostname or account:** press **Pair again** in the app.
- Questions: info@dnsalsa.nl
