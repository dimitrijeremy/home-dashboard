#!/bin/sh
# start_custom_stream.sh — custom channel launcher (versi Linux/server container)
# Dipanggil oleh backend saat user tambah channel CCTV custom via "Tambah" modal.
# Perbedaan dari versi Mac (start_custom_stream.sh di root):
#   - Encoder: -c:v copy (tidak perlu VideoToolbox)
#   - ffmpeg: plain 'ffmpeg' (dari apt-get)
#   - Target RTSP: gunakan MTX_HOST + RTSP_PORT (env var dari docker-compose)

set -eu

SOURCE_URL=${1:-}
PATH_NAME=${2:-}
RTSP_PORT_VALUE=${RTSP_PORT:-8554}
MTX_HOST_VALUE=${MTX_HOST:-localhost}
VIDEO_BITRATE=${CUSTOM_STREAM_VIDEO_BITRATE:-3000k}
MAXRATE_VALUE=${CUSTOM_STREAM_MAXRATE:-3500k}
BUFSIZE_VALUE=${CUSTOM_STREAM_BUFSIZE:-7000k}

# STREAM_QUALITY (env dari backend, setting 'stream_quality' di dashboard):
#   source (default) — tanpa scale; 720/480 — scale turun + bitrate lebih rendah;
#   sub — substream (subtype sudah diganti backend di URL), bitrate rendah.
SCALE_ARGS=''
case "${STREAM_QUALITY:-source}" in
  720)
    SCALE_ARGS='-vf scale=-2:720'
    VIDEO_BITRATE=${CUSTOM_STREAM_VIDEO_BITRATE:-1500k}
    MAXRATE_VALUE=${CUSTOM_STREAM_MAXRATE:-1800k}
    BUFSIZE_VALUE=${CUSTOM_STREAM_BUFSIZE:-3600k}
    ;;
  480)
    SCALE_ARGS='-vf scale=-2:480'
    VIDEO_BITRATE=${CUSTOM_STREAM_VIDEO_BITRATE:-800k}
    MAXRATE_VALUE=${CUSTOM_STREAM_MAXRATE:-1000k}
    BUFSIZE_VALUE=${CUSTOM_STREAM_BUFSIZE:-2000k}
    ;;
  sub)
    VIDEO_BITRATE=${CUSTOM_STREAM_VIDEO_BITRATE:-1000k}
    MAXRATE_VALUE=${CUSTOM_STREAM_MAXRATE:-1200k}
    BUFSIZE_VALUE=${CUSTOM_STREAM_BUFSIZE:-2400k}
    ;;
esac

if [ -z "$SOURCE_URL" ] || [ -z "$PATH_NAME" ]; then
  echo "usage: start_custom_stream.sh <source_rtsp_url> <path_name>" >&2
  exit 1
fi

ffmpeg_pid=''
socat_pid=''

cleanup() {
  if [ -n "$ffmpeg_pid" ]; then
    kill "$ffmpeg_pid" >/dev/null 2>&1 || true
    wait "$ffmpeg_pid" >/dev/null 2>&1 || true
  fi
  if [ -n "$socat_pid" ]; then
    kill "$socat_pid" >/dev/null 2>&1 || true
    wait "$socat_pid" >/dev/null 2>&1 || true
  fi
  exit 0
}

trap cleanup INT TERM

# Kamera dengan RTSP-over-TLS wajib (mis. Dahua DH-P5AE-PV) menolak request-line
# berskema rtsps:// yang dikirim ffmpeg dan langsung memutus koneksi. Solusi:
# socat membuka listener plaintext lokal dan meneruskan ke kamera lewat TLS,
# lalu ffmpeg konek biasa (rtsp://) ke listener itu. Sudah diverifikasi NVR
# Dahua juga menerima URI berhost 127.0.0.1, jadi aman untuk semua sumber rtsps.
TLS_BRIDGE=''
case "$SOURCE_URL" in
  rtsps://*)
    # user:pass@host:port dari URL; sisanya (path?query) dilewatkan apa adanya
    REST=${SOURCE_URL#rtsps://}
    HOSTPART=${REST%%/*}
    PATHPART=/${REST#*/}
    CRED=''
    case "$HOSTPART" in
      *@*) CRED=${HOSTPART%@*}@ ; HOSTPART=${HOSTPART##*@} ;;
    esac
    TLS_HOST=${HOSTPART%%:*}
    TLS_PORT=${HOSTPART#*:}
    if [ "$TLS_PORT" = "$TLS_HOST" ]; then TLS_PORT=554; fi
    # Port listener lokal deterministik per path (hindari bentrok antar stream)
    LOCAL_PORT=$((20000 + $(printf '%s' "$PATH_NAME" | cksum | cut -d' ' -f1) % 10000))
    TLS_BRIDGE=1
    SOURCE_URL="rtsp://${CRED}127.0.0.1:${LOCAL_PORT}${PATHPART}"
    echo "[STREAM] ${PATH_NAME}: rtsps via socat TLS bridge 127.0.0.1:${LOCAL_PORT} -> ${TLS_HOST}:${TLS_PORT}" >&2
    ;;
esac

# Pastikan socat hidup — dipanggil setiap iterasi loop. Saat relaunch, socat
# lama bisa masih memegang port sesaat sehingga socat baru gagal bind dan mati;
# tanpa restart di loop, ffmpeg akan "Connection refused" selamanya.
ensure_bridge() {
  if [ -z "$TLS_BRIDGE" ]; then return 0; fi
  if [ -n "$socat_pid" ] && kill -0 "$socat_pid" 2>/dev/null; then return 0; fi
  socat "TCP-LISTEN:${LOCAL_PORT},bind=127.0.0.1,reuseaddr,fork" \
        "OPENSSL:${TLS_HOST}:${TLS_PORT},verify=0" &
  socat_pid=$!
  sleep 1
}

while :; do
  ensure_bridge
  ffmpeg \
    -hide_banner -loglevel warning \
    -fflags +genpts+discardcorrupt \
    -use_wallclock_as_timestamps 1 \
    -rtsp_transport tcp \
    -i "$SOURCE_URL" \
    -map 0:v:0 \
    -dn \
    -an \
    $SCALE_ARGS \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -pix_fmt yuv420p \
    -g 25 \
    -keyint_min 25 \
    -force_key_frames 'expr:gte(t,n_forced*1)' \
    -sc_threshold 0 \
    -b:v "$VIDEO_BITRATE" \
    -maxrate "$MAXRATE_VALUE" \
    -bufsize "$BUFSIZE_VALUE" \
    -rtsp_transport tcp \
    -f rtsp "rtsp://${MTX_HOST_VALUE}:${RTSP_PORT_VALUE}/${PATH_NAME}" &
  ffmpeg_pid=$!

  set +e
  wait "$ffmpeg_pid"
  ffmpeg_exit=$?
  set -e

  ffmpeg_pid=''
  echo "[STREAM] ${PATH_NAME} ffmpeg exited with code ${ffmpeg_exit}, retrying in 3s" >&2
  sleep 3
done
