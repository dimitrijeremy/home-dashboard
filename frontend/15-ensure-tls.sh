#!/bin/sh
# Dijalankan otomatis oleh entrypoint nginx (/docker-entrypoint.d/) sebelum
# nginx start. Kalau volume tls belum diisi sertifikat asli (tls/gen-certs.sh
# di host), buat self-signed sementara supaya nginx tidak gagal start —
# browser akan warning sampai cert asli dibuat, tapi HTTP :8088 tetap normal.
set -e
TLS_DIR=/etc/nginx/tls
if [ ! -f "$TLS_DIR/server.crt" ] || [ ! -f "$TLS_DIR/server.key" ]; then
  echo "[tls] sertifikat tidak ditemukan — membuat self-signed sementara" >&2
  mkdir -p "$TLS_DIR"
  openssl req -x509 -newkey rsa:2048 -nodes -days 90 \
    -subj "/CN=home-dashboard-temp" \
    -keyout "$TLS_DIR/server.key" -out "$TLS_DIR/server.crt" 2>/dev/null
fi
