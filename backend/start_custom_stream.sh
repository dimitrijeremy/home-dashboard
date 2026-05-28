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

if [ -z "$SOURCE_URL" ] || [ -z "$PATH_NAME" ]; then
  echo "usage: start_custom_stream.sh <source_rtsp_url> <path_name>" >&2
  exit 1
fi

ffmpeg_pid=''

cleanup() {
  if [ -n "$ffmpeg_pid" ]; then
    kill "$ffmpeg_pid" >/dev/null 2>&1 || true
    wait "$ffmpeg_pid" >/dev/null 2>&1 || true
  fi
  exit 0
}

trap cleanup INT TERM

while :; do
  ffmpeg \
    -hide_banner -loglevel warning \
    -fflags +genpts+discardcorrupt \
    -use_wallclock_as_timestamps 1 \
    -rtsp_transport tcp \
    -i "$SOURCE_URL" \
    -map 0:v:0 \
    -dn \
    -an \
    -c:v libx264 \
    -preset ultrafast \
    -tune zerolatency \
    -pix_fmt yuv420p \
    -g 25 \
    -keyint_min 25 \
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
