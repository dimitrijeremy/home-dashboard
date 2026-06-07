FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ffmpeg diperlukan untuk launch custom RTSP stream ke mediamtx
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Dahua root CA — agar ffmpeg/curl bisa verifikasi sertifikat kamera Dahua (rtsps://)
COPY dahua-root.crt /usr/local/share/ca-certificates/dahua-root.crt
RUN update-ca-certificates

# GnuTLS: izinkan RSA key exchange untuk kompatibilitas kamera Dahua IP
# yang menggunakan TLS_RSA_WITH_AES_256_GCM_SHA384 (cipher lama tanpa forward secrecy)
RUN mkdir -p /etc/gnutls && echo 'NORMAL:+RSA' > /etc/gnutls/config

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# APP_DIR di app.py = dirname(dirname('/app/app.py')) = '/'
# Jadi CUSTOM_STREAM_SCRIPT default = /start_custom_stream.sh
# Kita taruh script Linux-compatible di sana supaya custom channel bisa jalan.
RUN cp /app/start_custom_stream.sh /start_custom_stream.sh && \
    chmod +x /start_custom_stream.sh

EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--worker-class", "gthread", "--threads", "8", "app:app"]
