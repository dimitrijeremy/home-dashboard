FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
  apt-get install -y --no-install-recommends ffmpeg ca-certificates curl python3 socat && \
    rm -rf /var/lib/apt/lists/*

# Dahua root CA — agar ffmpeg bisa verifikasi sertifikat kamera Dahua (rtsps://)
COPY dahua-root.crt /usr/local/share/ca-certificates/dahua-root.crt
RUN update-ca-certificates

# GnuTLS: izinkan RSA key exchange (tanpa PFS) untuk kompatibilitas kamera Dahua
# yang menggunakan TLS_RSA_WITH_AES_256_GCM_SHA384 (cipher lama tanpa forward secrecy)
RUN mkdir -p /etc/gnutls && echo 'NORMAL:+RSA' > /etc/gnutls/config

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

# Custom stream script — dipakai oleh backend untuk kamera IP langsung
COPY start_custom_stream.sh /app/start_custom_stream.sh
RUN chmod +x /app/start_custom_stream.sh

# Config + stream script di-mount via volume dari docker-compose
EXPOSE 8554 8888

CMD ["mediamtx", "/app/mediamtx.yml"]
