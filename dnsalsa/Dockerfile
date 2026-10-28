ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG CLOUDFLARED_VERSION

# cloudflared from Cloudflare's GitHub releases, checked against a pinned checksum.
# To update: change CLOUDFLARED_VERSION in build.yaml and both checksums below.
RUN apk add --no-cache python3 \
 && case "${BUILD_ARCH}" in \
      amd64)   ARCH=amd64; SUM=e1bc0cf084fd9e590554fe769a86b1db46701c3f847a43d902f7b974a4e16298 ;; \
      aarch64) ARCH=arm64; SUM=72e0d05a67e09da87cf495e409a28c3cc5994dd23fc66a982eebea6a55923b7d ;; \
      *) echo "Unsupported architecture: ${BUILD_ARCH}"; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
 && echo "${SUM}  /usr/local/bin/cloudflared" | sha256sum -c - \
 && chmod +x /usr/local/bin/cloudflared

COPY rootfs /
CMD ["python3", "-u", "/app/main.py"]
