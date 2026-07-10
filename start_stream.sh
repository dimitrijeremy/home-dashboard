#!/bin/sh
# start_stream.sh — dipanggil mediamtx runOnDemand per channel (versi host Mac)
# Usage: start_stream.sh <chN | N>   (mediamtx memanggil dengan $MTX_PATH, mis. "ch2")
# Host + credentials NVR diambil dari /api/nvr-config backend (menu konfigurasi
# dashboard); fallback ke env var. Tidak ada kredensial hardcode di file ini.
#
# CATATAN: sengaja TIDAK melakukan deteksi codec via ffprobe sebelum ffmpeg —
# itu berarti koneksi RTSPS kedua ke NVR (auth attempt ganda, berisiko lockout
# akun Dahua) dan tanpa timeout bisa menggantung sampai runOnDemandStartTimeout
# habis sehingga stream gagal tampil. Selalu transcode ke H.264.

RAW=$1
CH=$(printf '%s' "$RAW" | sed 's/^ch//')

if [ -z "$CH" ]; then
  echo "usage: start_stream.sh <chN | N>" >&2
  exit 1
fi

FFMPEG_BIN=${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}
DVR_HOST_VAL=${DVR_HOST:-}
DVR_USER_VAL=${DVR_USER:-}
DVR_PASS_VAL=${DVR_PASS:-}
STREAM_SUBTYPE=${DVR_STREAM_SUBTYPE:-0}
START_DELAY=${STREAM_START_DELAY:-5}
NVR_CONFIG_URL=${NVR_CONFIG_URL:-http://localhost:5001/api/nvr-config}

# Pacing delay: cegah auth attempt terlalu cepat ke NVR saat restart loop
# (Dahua bisa mengunci akun).
sleep "$START_DELAY"

CONFIG_JSON=$(curl -fsS --max-time 3 "$NVR_CONFIG_URL" 2>/dev/null || true)
if [ -n "$CONFIG_JSON" ]; then
  H=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("host") or "")' 2>/dev/null || true)
  U=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_user") or "")' 2>/dev/null || true)
  P=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_pass") or "")' 2>/dev/null || true)
  if [ -n "$H" ]; then DVR_HOST_VAL=$H; fi
  if [ -n "$U" ]; then DVR_USER_VAL=$U; fi
  if [ -n "$P" ]; then DVR_PASS_VAL=$P; fi
fi

if [ -z "$DVR_HOST_VAL" ]; then
  echo "[stream] NVR host belum dikonfigurasi (menu konfigurasi dashboard / env DVR_HOST)" >&2
  sleep 20
  exit 1
fi

SOURCE_URL="rtsps://${DVR_USER_VAL}:${DVR_PASS_VAL}@${DVR_HOST_VAL}:554/cam/realmonitor?channel=${CH}&subtype=${STREAM_SUBTYPE}&unicast=true&proto=Onvif&tls=true"

exec "$FFMPEG_BIN" \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp -tls_verify 0 \
  -i "$SOURCE_URL" \
  -c:v h264_videotoolbox -b:v 800k -profile:v main \
  -c:a aac -b:a 64k -af aresample=async=1 \
  -f rtsp rtsp://localhost:8554/ch${CH}
