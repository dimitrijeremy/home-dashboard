#!/bin/sh
# gen-certs.sh — buat CA lokal + sertifikat server untuk HTTPS dashboard.
#
# Pakai:  ./gen-certs.sh <ip-atau-host-server> [ip/host tambahan...]
# Contoh: ./gen-certs.sh 10.10.40.10 dashboard.local
#
# Hasil di tls/certs/:
#   ca.crt     — INI yang di-install sekali di tiap perangkat (profile/trust).
#   server.crt + server.key — dipakai nginx (di-mount ke container frontend).
#
# CA berlaku 10 tahun. Sertifikat server 800 hari — batas Apple untuk cert
# dari root non-publik adalah 825 hari, jadi "tanpa perawatan" penuh tidak
# mungkin di iPhone/Mac; tapi perpanjangan = jalankan ulang script ini saja
# (CA tidak berubah → perangkat TIDAK perlu install ulang apa pun).
#
# Setelah generate/perpanjang: docker compose restart frontend

set -eu
cd "$(dirname "$0")"

if [ $# -lt 1 ]; then
  echo "usage: $0 <ip-atau-host-server> [ip/host tambahan...]" >&2
  exit 1
fi

mkdir -p certs
cd certs

# ── CA (sekali seumur hidup, 10 tahun) ──────────────────────────────────────
if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 4096
  openssl req -x509 -new -key ca.key -sha256 -days 3650 \
    -subj "/CN=Home Dashboard CA" -out ca.crt
  chmod 600 ca.key
  echo "[tls] CA baru dibuat (berlaku 10 tahun): certs/ca.crt"
else
  echo "[tls] CA sudah ada — dipakai ulang (perangkat tidak perlu install ulang)"
fi

# ── SAN list dari argumen (IP vs DNS otomatis) ──────────────────────────────
SAN=""
i=1
for arg in "$@"; do
  case "$arg" in
    *[!0-9.]*) entry="DNS:${arg}" ;;
    *)         entry="IP:${arg}"  ;;
  esac
  SAN="${SAN:+$SAN,}$entry"
  i=$((i + 1))
done

# ── Sertifikat server (800 hari) ────────────────────────────────────────────
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=$1" -out server.csr
cat > server.ext <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=${SAN}
EOF
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -sha256 -days 800 -extfile server.ext -out server.crt
rm -f server.csr server.ext
chmod 600 server.key

echo
echo "[tls] Selesai. Berlaku sampai:"
openssl x509 -in server.crt -noout -enddate
echo
echo "Langkah berikutnya:"
echo "  1. docker compose restart frontend"
echo "  2. Install certs/ca.crt di tiap perangkat (lihat MONITORING.md § HTTPS)"
