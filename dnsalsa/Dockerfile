ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG CLOUDFLARED_VERSION

# cloudflared from Cloudflare's GitHub releases, checked against a pinned checksum.
# To update: change CLOUDFLARED_VERSION in build.yaml and both checksums below.
RUN apk add --no-cache python3 \
 && case "${BUILD_ARCH}" in \
      amd64)   ARCH=amd64; SUM=d33ff2d14475178d2012c2c56beba87389ac5ded27649519f198a7d3134a99db ;; \
      aarch64) ARCH=arm64; SUM=e6422b9d4f72d3194bc5a38676f13667c06666523217b842a877d72a80b5ac08 ;; \
      *) echo "Unsupported architecture: ${BUILD_ARCH}"; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
 && echo "${SUM}  /usr/local/bin/cloudflared" | sha256sum -c - \
 && chmod +x /usr/local/bin/cloudflared

COPY rootfs /
CMD ["python3", "-u", "/app/main.py"]
