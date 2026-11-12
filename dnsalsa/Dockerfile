ARG BUILD_FROM
FROM ${BUILD_FROM}

ARG BUILD_ARCH
ARG CLOUDFLARED_VERSION

# cloudflared from Cloudflare's GitHub releases, checked against a pinned checksum.
# Updated by .github/workflows/cloudflared.yml (a pull request every time Cloudflare releases a new version).
RUN apk add --no-cache python3 \
 && case "${BUILD_ARCH}" in \
      amd64)   ARCH=amd64; SUM=51f7c68b57ed7269164075d7e789efce5b59355982f06392deeb037d982a51fd ;; \
      aarch64) ARCH=arm64; SUM=abe54a006acf9faf2d19ee9d953cb0d0804e92f59c5382a162e57b84d086f56f ;; \
      *) echo "Unsupported architecture: ${BUILD_ARCH}"; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${ARCH}" \
 && echo "${SUM}  /usr/local/bin/cloudflared" | sha256sum -c - \
 && chmod +x /usr/local/bin/cloudflared

COPY rootfs /
CMD ["python3", "-u", "/app/main.py"]
