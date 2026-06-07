#!/bin/sh
# start_stream.sh — dipanggil mediamtx runOnInit per channel (versi Docker/Linux)
# Credentials diambil dari /api/nvr-config backend; fallback ke env var.
# Encoder: -c:v copy (tidak re-encode, hemat CPU, tidak butuh VideoToolbox)
#
# FIX: Dahua NVR mengekspirasi TLS session setiap ~90 detik → ffmpeg exit.
# Solusi: loop internal dengan retry 1s, bukan exec, agar restart secepat mungkin.

CH=$1

: "${DVR_HOST:?DVR_HOST env var harus di-set di docker-compose}"

DVR_USER_VAL=${DVR_USER:-dashboard}
DVR_PASS_VAL=${DVR_PASS:-}
STREAM_SUBTYPE=${DVR_STREAM_SUBTYPE:-0}

NVR_CONFIG_URL=${NVR_CONFIG_URL:-http://backend:5000/api/nvr-config}

# Stagger hanya saat PERTAMA startup agar tidak hit NVR bersamaan.
# Pada restart setelah crash: tidak ada sleep (RESTARTED env var di-set oleh loop).
if [ -z "$STREAM_RESTARTED" ]; then
  DELAY=$(( (CH - 1) * 4 + 3 ))
  sleep "$DELAY"

  CONFIG_JSON=$(curl -fsS --max-time 5 "$NVR_CONFIG_URL" 2>/dev/null || true)
  if [ -n "$CONFIG_JSON" ]; then
    U=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_user") or "")' 2>/dev/null || true)
    P=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_pass") or "")' 2>/dev/null || true)
    if [ -n "$U" ]; then DVR_USER_VAL=$U; fi
    if [ -n "$P" ]; then DVR_PASS_VAL=$P; fi
  fi
fi

ENC_PASS=$(printf '%s' "${DVR_PASS_VAL}" | python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.stdin.read(),safe=""))')
LOCAL_PORT=$(( 11550 + CH ))
RTSP_PATH="/cam/realmonitor?channel=${CH}&subtype=${STREAM_SUBTYPE}&unicast=true&proto=Onvif&tls=true"
RTSP_SRC="rtsp://${DVR_USER_VAL}:${ENC_PASS}@127.0.0.1:${LOCAL_PORT}${RTSP_PATH}"

# Loop internal: restart ffmpeg secepat mungkin (<1s) saat TLS session expire.
# NVR Dahua lebih stabil jika TLS ditangani OpenSSL/socat; ffmpeg Ubuntu memakai
# GnuTLS dan sering putus periodik pada firmware/cipher lama.
while true; do
  socat \
    "TCP-LISTEN:${LOCAL_PORT},reuseaddr" \
    "OPENSSL:${DVR_HOST}:554,verify=0,cafile=/etc/ssl/certs/ca-certificates.crt" \
    2>/dev/null &
  SOCAT_PID=$!
  sleep 0.3

  ffmpeg \
    -hide_banner -loglevel warning \
    -rtsp_transport tcp \
    -i "$RTSP_SRC" \
    -c:v copy \
    -c:a aac -b:a 64k \
    -rtsp_transport tcp \
    -f rtsp "rtsp://localhost:8554/ch${CH}"
  EXIT_CODE=$?
  kill "$SOCAT_PID" 2>/dev/null || true
  wait "$SOCAT_PID" 2>/dev/null || true
  echo "[ch${CH}] ffmpeg exited (code ${EXIT_CODE}), retrying in 1s..." >&2
  sleep 1
  export STREAM_RESTARTED=1
done
