#!/bin/sh
# start_custom_stream.sh — dipanggil mediamtx runOnInit untuk custom camera stream
# Arg1: full RTSP source URL (boleh rtsps:// untuk kamera dengan TLS)
# Arg2: MTX path name (mis. custom_abc123)
#
# Untuk kamera rtsps://:
#   GnuTLS (ffmpeg Ubuntu 22.04) tidak kompatibel dengan TLS lama Dahua IP camera
#   (AES256-GCM RSA cipher). Solusi: socat sebagai TCP -> TLS tunnel via OpenSSL.
#   Setiap stream mendapat port lokal unik agar tidak konflik.

SOURCE_URL="${1:-}"
PATH_NAME="${2:-}"

if [ -z "$SOURCE_URL" ] || [ -z "$PATH_NAME" ]; then
  echo "usage: start_custom_stream.sh <source_rtsp_url> <path_name>" >&2
  exit 1
fi

# Port lokal unik berbasis hash nama path, range 12000-14999
LOCAL_PORT=$(( 12000 + $(printf '%s' "$PATH_NAME" | cksum | cut -d' ' -f1) % 3000 ))

run_one() {
  local src="$1"
  local path="$2"
  local lport="$3"

  case "$src" in
    rtsps://*)
      # Ekstrak host dan port kamera dari URL
      local noproto="${src#rtsps://}"
      local hostpart="${noproto##*@}"      # buang user:pass@
      hostpart="${hostpart%%/*}"           # buang /path
      local cam_host="${hostpart%%:*}"
      local cam_port="${hostpart##*:}"
      [ "$cam_port" = "$cam_host" ] && cam_port=554

      # socat tunnel satu koneksi: TCP -> TLS via OpenSSL (bukan GnuTLS)
      socat \
        "TCP-LISTEN:${lport},reuseaddr" \
        "OPENSSL:${cam_host}:${cam_port},verify=0,cafile=/etc/ssl/certs/ca-certificates.crt" \
        2>/dev/null &
      local socat_pid=$!
      sleep 0.3

      # Ganti rtsps://user:pass@host:port -> rtsp://user:pass@127.0.0.1:lport
      local plain_url
      plain_url=$(printf '%s' "$src" | sed \
        "s|rtsps://\([^@]*@\)${cam_host}:${cam_port}|rtsp://\1127.0.0.1:${lport}|")

      ffmpeg \
        -hide_banner -loglevel warning \
        -rtsp_transport tcp \
        -i "$plain_url" \
        -map 0:v:0 -dn -an \
        -c:v copy \
        -rtsp_transport tcp \
        -f rtsp "rtsp://localhost:8554/${path}" || true

      kill "$socat_pid" 2>/dev/null || true
      wait "$socat_pid" 2>/dev/null || true
      ;;
    *)
      # Plain RTSP -- tidak butuh tunnel
      ffmpeg \
        -hide_banner -loglevel warning \
        -rtsp_transport tcp \
        -i "$src" \
        -map 0:v:0 -dn -an \
        -c:v copy \
        -rtsp_transport tcp \
        -f rtsp "rtsp://localhost:8554/${path}" || true
      ;;
  esac
}

# Loop internal: restart saat koneksi putus atau TLS session expire
while true; do
  run_one "$SOURCE_URL" "$PATH_NAME" "$LOCAL_PORT"
  echo "[custom $PATH_NAME] exited, retrying in 2s..." >&2
  sleep 2
done
