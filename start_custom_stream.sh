#!/bin/sh
# start_custom_stream.sh — launcher untuk publisher custom.
# Usage: start_custom_stream.sh <source_rtsp_url> <path_name>

set -eu

SOURCE_URL=${1:-}
PATH_NAME=${2:-}
RTSP_PORT_VALUE=${RTSP_PORT:-8554}

if [ -z "$SOURCE_URL" ] || [ -z "$PATH_NAME" ]; then
  echo "usage: start_custom_stream.sh <source_rtsp_url> <path_name>" >&2
  exit 1
fi

exec /opt/homebrew/bin/ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp -tls_verify 0 \
  -i "$SOURCE_URL" \
  -c:v h264_videotoolbox -b:v 800k -profile:v main \
  -c:a aac -b:a 64k -af aresample=async=1 \
  -f rtsp "rtsp://localhost:${RTSP_PORT_VALUE}/${PATH_NAME}"