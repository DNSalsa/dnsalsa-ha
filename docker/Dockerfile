# DNSalsa as a plain Docker container, for Home Assistant Container or Core (no apps/add-ons).
# Same code as the Home Assistant app. Published as ghcr.io/dnsalsa/dnsalsa-ha by .github/workflows/publish.yml.
# Or build it yourself straight from GitHub:
#   docker build -t dnsalsa -f docker/Dockerfile https://github.com/DNSalsa/dnsalsa-ha.git#main
FROM alpine:3.23

ARG TARGETARCH
# Keep in step with dnsalsa/build.yaml and dnsalsa/Dockerfile; .github/workflows/cloudflared.yml updates all three.
ARG CLOUDFLARED_VERSION=2026.10.5

RUN apk add --no-cache python3 ca-certificates curl \
 && case "${TARGETARCH}" in \
      amd64)   ARCH=amd64; SUM=e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298 ;; \
      arm64)   ARCH=arm64; SUM=72e0d05a67e09da87cf495e409a28c3cc5994dd23fc66a982eebea6a55923b7d ;; \
      *) echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
 && echo "${SUM}  /usr/local/bin/cloudflared" | sha256sum -c - \
 && chmod +x /usr/local/bin/cloudflared \
 && apk del curl \
 && adduser -D -H -u 1000 dnsalsa \
 && mkdir /data && chown dnsalsa:dnsalsa /data

COPY dnsalsa/rootfs/app /app

ENV DNSALSA_STANDALONE=1 \
    DNSALSA_DATA=/data \
    DNSALSA_HA_URL=http://homeassistant:8123
USER dnsalsa
VOLUME /data
EXPOSE 8099
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8099/status',timeout=3)"
CMD ["python3", "-u", "/app/main.py"]
