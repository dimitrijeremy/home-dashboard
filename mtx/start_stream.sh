#!/bin/sh
# start_stream.sh — dipanggil mediamtx runOnDemand per channel (versi Docker/Linux)
# Usage: start_stream.sh <chN | N>   (mediamtx memanggil dengan $MTX_PATH, mis. "ch2")
# Host + credentials NVR diambil dari /api/nvr-config backend; fallback ke env var.
#
# CATATAN: sengaja TIDAK melakukan deteksi codec via ffprobe sebelum ffmpeg.
# Itu berarti koneksi RTSPS terpisah ke NVR (auth attempt ganda — berisiko
# memicu lockout akun Dahua) dan tanpa timeout bisa menggantung sampai
# runOnDemandStartTimeout habis, sehingga stream gagal tampil sama sekali.
# Selalu transcode ke H.264 supaya kompatibel dengan browser via HLS.

RAW=$1
CH=$(printf '%s' "$RAW" | sed 's/^ch//')

if [ -z "$CH" ]; then
  echo "usage: start_stream.sh <chN | N>" >&2
  exit 1
fi

DVR_HOST_VAL=${DVR_HOST:-}
DVR_USER_VAL=${DVR_USER:-}
DVR_PASS_VAL=${DVR_PASS:-}
STREAM_SUBTYPE=${DVR_STREAM_SUBTYPE:-0}
VIDEO_BITRATE=${STREAM_VIDEO_BITRATE:-2000k}
MAXRATE_VALUE=${STREAM_MAXRATE:-2500k}
BUFSIZE_VALUE=${STREAM_BUFSIZE:-5000k}
# Pacing delay: cegah auth attempt terlalu cepat ke NVR saat restart loop
# (Dahua bisa mengunci akun). Jangan terlalu besar — ini juga menambah waktu
# tunggu viewer pertama pada mode on-demand.
START_DELAY=${STREAM_START_DELAY:-5}

# Ambil host + credentials dari backend config API (menu konfigurasi dashboard)
NVR_CONFIG_URL=${NVR_CONFIG_URL:-http://backend:5000/api/nvr-config}

sleep "$START_DELAY"

CONFIG_JSON=$(curl -fsS --max-time 5 "$NVR_CONFIG_URL" 2>/dev/null || true)
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

# CATATAN: jangan tambahkan -tls_verify di sini — ffmpeg 4.4 (Ubuntu 22.04)
# tidak mengenal opsi itu untuk input RTSP dan akan exit dengan
# "Option tls_verify not found" justru SETELAH koneksi berhasil.
# Verifikasi sertifikat TLS ffmpeg memang sudah off secara default.
exec ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp \
  -i "$SOURCE_URL" \
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
  -c:a aac -b:a 64k \
  -rtsp_transport tcp \
  -f rtsp "rtsp://localhost:8554/ch${CH}"
