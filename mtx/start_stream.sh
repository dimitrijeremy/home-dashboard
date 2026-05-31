#!/bin/sh
# start_stream.sh — dipanggil mediamtx runOnInit per channel (versi Docker/Linux)
# Credentials diambil dari /api/nvr-config backend; fallback ke env var.
# Encoder: -c:v copy (tidak re-encode, hemat CPU, tidak butuh VideoToolbox)

CH=$1

: "${DVR_HOST:?DVR_HOST env var harus di-set di docker-compose}"

DVR_USER_VAL=${DVR_USER:-dashboard}
DVR_PASS_VAL=${DVR_PASS:-}
STREAM_SUBTYPE=${DVR_STREAM_SUBTYPE:-0}

# Ambil credentials dari backend config API (config menu dashboard)
NVR_CONFIG_URL=${NVR_CONFIG_URL:-http://backend:5000/api/nvr-config}

# Pacing delay: cegah auth attempt terlalu cepat ke NVR saat restart loop
sleep 15

CONFIG_JSON=$(curl -fsS --max-time 5 "$NVR_CONFIG_URL" 2>/dev/null || true)
if [ -n "$CONFIG_JSON" ]; then
  U=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_user") or "")' 2>/dev/null || true)
  P=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_pass") or "")' 2>/dev/null || true)
  if [ -n "$U" ]; then DVR_USER_VAL=$U; fi
  if [ -n "$P" ]; then DVR_PASS_VAL=$P; fi
fi


exec ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp \
  -i "rtsps://${DVR_USER_VAL}:${DVR_PASS_VAL}@${DVR_HOST}:554/cam/realmonitor?channel=${CH}&subtype=${STREAM_SUBTYPE}&unicast=true&proto=Onvif&tls=true" \
  -c:v libx264 \
  -preset ultrafast \
  -tune zerolatency \
  -pix_fmt yuv420p \
  -g 25 \
  -keyint_min 25 \
  -sc_threshold 0 \
  -b:v 3000k \
  -maxrate 3500k \
  -bufsize 7000k \
  -c:a aac -b:a 64k \
  -rtsp_transport tcp \
  -f rtsp rtsp://localhost:8554/ch${CH}
