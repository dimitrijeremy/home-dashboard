FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# intel-media-va-driver-non-free (iHD, VAAPI/Quick Sync) — repo multiverse,
# amd64 saja. Install best-effort (|| true): kalau paketnya tidak ketemu untuk
# alasan apa pun, build TETAP lanjut, hanya HWACCEL=vaapi yang tidak akan
# jalan di container ini (software encode default tidak terpengaruh).
RUN apt-get update && \
  apt-get install -y --no-install-recommends ffmpeg ca-certificates curl python3 vainfo && \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      apt-get install -y --no-install-recommends intel-media-va-driver-non-free || \
        echo "[build] intel-media-va-driver-non-free tidak tersedia — HWACCEL=vaapi tidak akan jalan di container ini" >&2; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Download mediamtx binary — deteksi otomatis arsitektur container (amd64 / arm64)
ARG MTX_VERSION=v1.9.0
RUN ARCH="$(uname -m)"; \
    case "$ARCH" in \
      aarch64|arm64) MTXARCH="arm64v8" ;; \
      *)             MTXARCH="amd64"   ;; \
    esac && \
    curl -sL \
      "https://github.com/bluenviron/mediamtx/releases/download/${MTX_VERSION}/mediamtx_${MTX_VERSION}_linux_${MTXARCH}.tar.gz" \
      | tar xz -C /usr/local/bin mediamtx && \
    chmod +x /usr/local/bin/mediamtx

WORKDIR /app

# Config + stream script di-mount via volume dari docker-compose
EXPOSE 8554 8888

CMD ["mediamtx", "/app/mediamtx.yml"]
