ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG CLOUDFLARED_VERSION

# cloudflared from Cloudflare's GitHub releases, checked against a pinned checksum.
# Updated by .github/workflows/cloudflared.yml (a pull request every time Cloudflare releases a new version).
RUN apk add --no-cache python3 \
 && case "${BUILD_ARCH}" in \
      amd64)   ARCH=amd64; SUM=1f99fb518b83dec2ba8d870a7cc991d82a77e6196295a3d2754ba17ab9f91872 ;; \
      aarch64) ARCH=arm64; SUM=3df43aedc9ff291d1735b5cb576cb97e0b157488796db2e32b28aecabb445775 ;; \
      *) echo "Unsupported architecture: ${BUILD_ARCH}"; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
 && echo "${SUM}  /usr/local/bin/cloudflared" | sha256sum -c - \
 && chmod +x /usr/local/bin/cloudflared

COPY rootfs /
CMD ["python3", "-u", "/app/main.py"]
