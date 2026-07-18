FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ffmpeg diperlukan untuk launch custom RTSP stream ke mediamtx
# socat: TLS bridge untuk kamera yang mewajibkan RTSPS tapi menolak request-line
# berskema rtsps:// dari ffmpeg (mis. Dahua DH-P5AE-PV)
# intel-media-va-driver-non-free (iHD, VAAPI/Quick Sync): best-effort saja.
# Base image ini (Debian slim) tidak mengaktifkan komponen "non-free" secara
# default, jadi paket ini kemungkinan besar tidak akan ketemu di sini — install
# di-skip diam-diam (|| true) supaya build backend TIDAK PERNAH gagal gara-gara
# ini. Kalau HWACCEL=vaapi butuh jalan juga untuk custom channel (bukan cuma
# channel NVR lewat mtx), aktifkan komponen non-free di sources Debian dulu.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg socat vainfo && \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
      apt-get install -y --no-install-recommends intel-media-va-driver-non-free || \
        echo "[build] intel-media-va-driver-non-free tidak tersedia — HWACCEL=vaapi tidak akan jalan di container ini" >&2; \
    fi && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# APP_DIR di app.py = dirname(dirname('/app/app.py')) = '/'
# Jadi CUSTOM_STREAM_SCRIPT default = /start_custom_stream.sh
# Kita taruh script Linux-compatible di sana supaya custom channel bisa jalan.
RUN cp /app/start_custom_stream.sh /start_custom_stream.sh && \
    chmod +x /start_custom_stream.sh

EXPOSE 5000
# --timeout lebih longgar dari default (30s) — beberapa endpoint (mis.
# /api/nvr-info yang probe NVR langsung) bisa sah butuh waktu lebih lama,
# dan worker yang di-kill di tengah request panjang cuma bikin retry-loop.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--threads", "8", "--timeout", "90", "app:app"]
