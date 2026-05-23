#!/bin/sh
# start_stream.sh — dipanggil oleh mediamtx runOnInit per channel
# Usage: start_stream.sh <channel_number>
# Credentials disimpan di sini sehingga mediamtx YAML tidak perlu mengandung $
# (mediamtx mengexpand $VAR sebelum passing ke shell, sehingga d4$hb0ard hilang)

CH=$1
DVR_HOST=${DVR_HOST:-10.10.30.2}
DVR_USER=${DVR_USER:-dashboard}
DVR_PASS=${DVR_PASS:-'d4$hb0ard-dlt'}   # fallback only; config menu is preferred
NVR_CONFIG_URL=${NVR_CONFIG_URL:-http://localhost:5001/api/nvr-config}

# Channel 1-4 credentials come from the dashboard config menu.
# If backend is still booting, fall back to env/defaults so MediaMTX can retry.
CONFIG_JSON=$(curl -fsS --max-time 3 "$NVR_CONFIG_URL" 2>/dev/null || true)
if [ -n "$CONFIG_JSON" ]; then
  CONFIG_CREDS=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json, sys; d=json.load(sys.stdin); print(d.get("stream_user") or ""); print(d.get("stream_pass") or "")' 2>/dev/null || true)
  CONFIG_USER=$(printf '%s\n' "$CONFIG_CREDS" | sed -n '1p')
  CONFIG_PASS=$(printf '%s\n' "$CONFIG_CREDS" | sed -n '2p')
  if [ -n "$CONFIG_USER" ]; then DVR_USER=$CONFIG_USER; fi
  if [ -n "$CONFIG_PASS" ]; then DVR_PASS=$CONFIG_PASS; fi
fi

SOURCE_URL="rtsps://${DVR_USER}:${DVR_PASS}@${DVR_HOST}:554/cam/realmonitor?channel=${CH}&subtype=0&unicast=true&proto=Onvif&tls=true"

# ── Pacing delay ─────────────────────────────────────────────────────────────
# mediamtx runOnInitRestart: yes akan restart script ini langsung saat keluar.
# Tambahkan jeda 15s agar auth attempt ke NVR tidak berulang terlalu cepat.
# Tanpa ini, ffmpeg restart bisa > 60x/menit dan trigger Dahua account lock.
sleep 15

exec /opt/homebrew/bin/ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp -tls_verify 0 \
  -i "$SOURCE_URL" \
  -c:v h264_videotoolbox -b:v 800k -profile:v main \
  -c:a aac -b:a 64k -af aresample=async=1 \
  -f rtsp rtsp://localhost:8554/ch${CH}
