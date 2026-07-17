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
  Q=$(printf '%s' "$CONFIG_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("stream_quality") or "")' 2>/dev/null || true)
  if [ -n "$H" ]; then DVR_HOST_VAL=$H; fi
  if [ -n "$U" ]; then DVR_USER_VAL=$U; fi
  if [ -n "$P" ]; then DVR_PASS_VAL=$P; fi
fi

# Kualitas stream dari setting dashboard (stream_quality):
#   source — main stream tanpa scale; 720/480 — scale turun + bitrate rendah;
#   sub — substream NVR (subtype=1), paling hemat CPU/bandwidth.
SCALE_ARGS=''
case "${Q:-source}" in
  sub)
    STREAM_SUBTYPE=1
    VIDEO_BITRATE=${STREAM_VIDEO_BITRATE:-1000k}
    MAXRATE_VALUE=${STREAM_MAXRATE:-1200k}
    BUFSIZE_VALUE=${STREAM_BUFSIZE:-2400k}
    ;;
  720)
    SCALE_ARGS='-vf scale=-2:720'
    VIDEO_BITRATE=${STREAM_VIDEO_BITRATE:-1500k}
    MAXRATE_VALUE=${STREAM_MAXRATE:-1800k}
    BUFSIZE_VALUE=${STREAM_BUFSIZE:-3600k}
    ;;
  480)
    SCALE_ARGS='-vf scale=-2:480'
    VIDEO_BITRATE=${STREAM_VIDEO_BITRATE:-800k}
    MAXRATE_VALUE=${STREAM_MAXRATE:-1000k}
    BUFSIZE_VALUE=${STREAM_BUFSIZE:-2000k}
    ;;
esac

if [ -z "$DVR_HOST_VAL" ]; then
  echo "[stream] NVR host belum dikonfigurasi (menu konfigurasi dashboard / env DVR_HOST)" >&2
  sleep 20
  exit 1
fi

SOURCE_URL="rtsps://${DVR_USER_VAL}:${DVR_PASS_VAL}@${DVR_HOST_VAL}:554/cam/realmonitor?channel=${CH}&subtype=${STREAM_SUBTYPE}&unicast=true&proto=Onvif&tls=true"

# HWACCEL=vaapi (opsional, set di .env): pakai Intel Quick Sync untuk encode
# H.264 (decode tetap software — cukup ringan, yang mahal itu encode-nya).
# Default kosong = software libx264 seperti semula, tidak ada perubahan
# perilaku kalau HWACCEL tidak di-set. Syarat: /dev/dri di-passthrough ke
# container (docker-compose `devices:`) dan node punya iGPU Intel yang aktif
# — verifikasi dulu dengan `vainfo` di dalam container sebelum mengandalkan ini.
VAAPI_DEVICE=${VAAPI_DEVICE:-/dev/dri/renderD128}

# CATATAN: jangan tambahkan -tls_verify di sini — ffmpeg 4.4 (Ubuntu 22.04)
# tidak mengenal opsi itu untuk input RTSP dan akan exit dengan
# "Option tls_verify not found" justru SETELAH koneksi berhasil.
# Verifikasi sertifikat TLS ffmpeg memang sudah off secara default.
if [ "${HWACCEL:-}" = "vaapi" ]; then
  exec ffmpeg \
    -hide_banner -loglevel warning \
    -vaapi_device "$VAAPI_DEVICE" \
    -rtsp_transport tcp \
    -i "$SOURCE_URL" \
    -vf "${SCALE_ARGS:+${SCALE_ARGS#-vf },}format=nv12,hwupload" \
    -c:v h264_vaapi \
    -g 25 \
    -keyint_min 25 \
    -bf 0 \
    -b:v "$VIDEO_BITRATE" \
    -maxrate "$MAXRATE_VALUE" \
    -bufsize "$BUFSIZE_VALUE" \
    -c:a aac -b:a 64k \
    -rtsp_transport tcp \
    -f rtsp "rtsp://localhost:8554/ch${CH}"
else
  exec ffmpeg \
    -hide_banner -loglevel warning \
    -rtsp_transport tcp \
    -i "$SOURCE_URL" \
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
    -c:a aac -b:a 64k \
    -rtsp_transport tcp \
    -f rtsp "rtsp://localhost:8554/ch${CH}"
fi
