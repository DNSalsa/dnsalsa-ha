# Changelog

## 1.0.6

- No apps in your Home Assistant? DNSalsa now also runs as a Docker container: `ghcr.io/dnsalsa/dnsalsa-ha`.

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
