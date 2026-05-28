# Analisa Masalah Deployment — Home Dashboard

> Tanggal: 23 Mei 2026  
> Status: **Sudah diperbaiki**

---

## Ringkasan Masalah

Dashboard muncul error **"Backend: Failed to fetch cameras"** saat dijalankan. Masalah bukan satu tapi empat sekaligus, semua saling berkaitan.

---

## Bug #1 — Frontend tidak bisa reach `/api/*` di server (ROOT CAUSE)

### Kondisi sebelumnya
Frontend di-serve via **`npm run dev`** (Vite dev server). Proxy `/api → http://localhost:5001` ada di `vite.config.js`, tapi **hanya berlaku di dev server** — bukan di built SPA.

Di server, browser langsung fetch `GET /api/cameras` ke **`http://<server-ip>/api/cameras`** yang tidak ada karena tidak ada reverse proxy.

### Kenapa terjadi
`docker-compose.yml` meng-expose port `5173` dan menjalankan `npm run dev` di container. Ini mode development, tidak produksi.

### Fix yang diterapkan
- **Dockerfile frontend** diganti ke **multi-stage build**:
  1. Stage 1 (`node:18`): build static assets via `npm run build`
  2. Stage 2 (`nginx:1.27-alpine`): serve static + proxy `/api/` ke `backend:5000`
- Ditambahkan **`nginx.conf`** yang menangani:
  - Proxy `/api/*` → `http://backend:5000`
  - SPA fallback (`try_files $uri /index.html`)
- Port frontend di compose diubah dari `5173:5173` → `80:80`

---

## Bug #2 — `BASE_URL=http://localhost:8888` tidak valid di dalam container

### Kondisi sebelumnya
```yaml
- BASE_URL=http://localhost:8888
```

### Kenapa salah
`localhost` di dalam container backend **bukan host machine**. Container tidak bisa reach MediaMTX (yang jalan di host) via `localhost`. Akibatnya URL stream kamera yang di-seed (`http://localhost:8888/ch1/index.m3u8`) tidak bisa diakses, dan stream status selalu offline.

### Fix
```yaml
- BASE_URL=http://host.docker.internal:8888
```
Dikombinasikan dengan:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
`host-gateway` adalah alias Docker untuk IP host machine, tersedia di Linux maupun Mac.

---

## Bug #3 — Kredensial `DVR_USER` tidak konsisten

### Kondisi sebelumnya
`docker-compose.yml`:
```yaml
- DVR_USER=dashboard
```
`start_stream.sh`:
```sh
DVR_USER=dashboard2
DVR_PASS='d4$h0ard-dlt'
```

### Dampak
- Backend mencatat user `dashboard` untuk snapshot/event HTTP
- ffmpeg (via `start_stream.sh`) pakai `dashboard2`
- Dua akun berbeda digunakan tanpa sadar; dapat menyebabkan lockout salah satu akun

### Fix
`docker-compose.yml` diseragamkan ke `DVR_USER=dashboard2` dan `DVR_PASS=d4$$hb0ard-dlt` (double `$$` karena Docker Compose mengexpand `$`).

---

## Bug #4 — `DVR_HTTP_PORT` tidak ada di compose

### Kondisi
Backend membaca `DVR_HTTP_PORT` dari env, tapi variable ini tidak ada di compose sebelumnya. Backend fallback ke `80` (hardcoded default), kebetulan benar — tapi tidak eksplisit dan tidak portable.

### Fix
Ditambahkan `DVR_HTTP_PORT=80` secara eksplisit ke compose.

---

## Ringkasan Perubahan File

| File | Perubahan |
|---|---|
| `frontend/Dockerfile` | Multi-stage build (Node build → Nginx serve) |
| `frontend/nginx.conf` | **Baru**: proxy `/api/` ke backend, SPA fallback |
| `docker-compose.yml` | Fix `BASE_URL`, `DVR_USER`, tambah `DVR_HTTP_PORT`, `extra_hosts`, port frontend `80:80` |

---

## Cara Deploy ke Server

### Prasyarat Server
- Docker + Docker Compose v2 terinstall
- Port 80 dan 5001 terbuka di firewall (5001 opsional, hanya untuk debug)
- MediaMTX terinstall dan berjalan di host server (bukan container), karena NVREventLog butuh akses RTSP langsung ke NVR dari host
- Akun NVR `dashboard2` aktif dengan password yang benar

### Langkah Deploy

```bash
# 1. Clone / upload project ke server
git clone <repo> home-dashboard
cd home-dashboard/home-dashboard

# 2. (Opsional) Sesuaikan IP NVR dan kredensial di docker-compose.yml
#    jika berbeda dari 10.10.30.2 / dashboard2

# 3. Build dan jalankan
docker compose build
docker compose up -d

# 4. Cek log
docker compose logs -f backend
docker compose logs -f frontend
```

Buka `http://<server-ip>` → dashboard langsung tersedia.

### Jalankan MediaMTX di Host Server

```bash
cd /path/ke/home-dashboard/home-dashboard

# Edit mediamtx.yml: sesuaikan path start_stream.sh ke path server
./mediamtx mediamtx.yml &
```

> **Catatan**: `mediamtx.yml` menyimpan path absolut ke `start_stream.sh`. Harus diupdate sesuai path di server.

---

## Hal yang Masih Perlu Diperhatikan

| Item | Keterangan |
|---|---|
| Path `start_stream.sh` di `mediamtx.yml` | Hardcoded `/Users/dimitrijeremy/...` — harus disesuaikan di server |
| `start_stream.sh` pakai `h264_videotoolbox` | Encoder hardware Apple Silicon — tidak tersedia di server Linux. Ganti ke `-c:v copy` atau `-c:v libx264` |
| Tidak ada autentikasi | Dashboard bisa diakses siapa saja di jaringan. Pertimbangkan Nginx basic auth atau VPN |
| `ANALYZER` profile | Opsional, tidak ikut `docker compose up` biasa |
