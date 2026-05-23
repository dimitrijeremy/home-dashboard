# home-dashboard

Dashboard rumah dengan CCTV live, kontrol smart home, cuaca, NVR event stream, dan AI deteksi perimeter (YOLOv8 + face recognition).

---

## Arsitektur

```
Dahua NVR (RTSPS :554)
    │
    └── ffmpeg (VideoToolbox H.264/AAC)
            │
        MediaMTX (host) ──► RTSP :8554 ──► analyzer service (AI, opsional)
            │
           HLS :8888
            │
        backend container (Flask :5001)
            │
        frontend container (React+Vite :5173)
```

---

## Layanan / Container

| Nama | Cara jalankan | Port | Keterangan |
|------|--------------|------|------------|
| **mediamtx** | Host binary | RTSP `8554`, HLS `8888` | Terima stream ffmpeg→RTSPS dari NVR, publikasikan ke HLS. Harus jalan di host (bukan container) karena butuh VideoToolbox. |
| **backend** | Docker Compose | `5001` | Flask API: kamera, zona, wajah, event NVR, event deteksi AI. Data disimpan di volume `camera_data`. |
| **frontend** | Docker Compose | `5173` | React + Vite. Dashboard utama + halaman konfigurasi AI. |
| **analyzer** | Docker Compose (profile `ai`) | — | YOLOv8n + InsightFace. Baca RTSP dari mediamtx, kirim event ke backend. **Opsional.** |

---

## Cara Menjalankan

### 1. Jalankan MediaMTX (di host)

MediaMTX harus jalan di host Mac karena ffmpeg pakai **VideoToolbox** hardware encoder yang tidak tersedia dalam container.

```bash
cd /path/to/home-dashboard/home-dashboard

# Jalankan di background
./mediamtx mediamtx.yml &
```

MediaMTX akan otomatis memanggil `ffmpeg` via `runOnInit` untuk tiap channel saat ada viewer, mengambil stream RTSPS dari NVR dan mempublikasikan sebagai HLS di `http://localhost:8888/ch1/index.m3u8`.

### 2. Jalankan Backend + Frontend (Docker Compose)

```bash
cd /path/to/home-dashboard/home-dashboard

# Build (pertama kali atau setelah ada perubahan kode)
docker compose build

# Jalankan
docker compose up -d

# Lihat log
docker compose logs -f
```

Buka **http://localhost:5173** di browser.

### 3. Jalankan Analyzer AI (opsional)

Analyzer membutuhkan build lebih lama (~5 menit) karena mengunduh PyTorch CPU dan model YOLOv8.

```bash
# Build + jalankan hanya analyzer
docker compose --profile ai up -d --build analyzer

# Atau semua sekaligus (backend + frontend + analyzer)
docker compose --profile ai up -d --build
```

Setelah analyzer jalan, buka halaman **⚙ Konfigurasi** di dashboard untuk:
- Menggambar zona perimeter per kamera
- Mendaftarkan wajah penghuni
- Melihat riwayat event deteksi

---

## Konfigurasi Environment

### Backend (`docker-compose.yml` → service `backend`)

| Variable | Default | Keterangan |
|----------|---------|------------|
| `BASE_URL` | `http://localhost:8888` | URL HLS mediamtx (dilihat dari dalam container, gunakan `http://host.docker.internal:8888` jika perlu) |
| `DB_PATH` | `/data/cameras.db` | Path SQLite database (di dalam volume) |
| `FACE_PHOTO_DIR` | `/data/face_photos` | Direktori foto wajah terdaftar |
| `SNAPSHOT_DIR` | `/data/snapshots` | Direktori snapshot kamera dari analyzer |
| `DVR_HOST` | `10.10.30.2` | IP Dahua NVR |
| `DVR_HTTP_PORT` | `80` | Port HTTP NVR |
| `DVR_USER` | `dashboard` | User untuk RTSP (dibaca stream) |
| `DVR_PASS` | `d4$$hb0ard-dlt` | Password DVR_USER (di compose gunakan `$$` untuk karakter `$`) |
| `DVR_EVENT_USER` | _(sama dengan DVR_USER)_ | User untuk NVR event stream. Butuh hak **Remote Alarm/Event**. Kosongkan untuk fallback ke DVR_USER. |
| `DVR_EVENT_PASS` | _(sama dengan DVR_PASS)_ | Password DVR_EVENT_USER |

### Analyzer (`docker-compose.yml` → service `analyzer`)

| Variable | Default | Keterangan |
|----------|---------|------------|
| `BACKEND_URL` | `http://backend:5000` | URL internal backend |
| `MTX_RTSP` | `rtsp://host.docker.internal:8554` | Base URL RTSP mediamtx |
| `SNAPSHOT_DIR` | `/data/snapshots` | Direktori simpan snapshot |
| `PROCESS_EVERY` | `8` | Proses 1 frame setiap N frame (hemat CPU) |
| `FACE_THRESH` | `0.40` | Threshold similarity untuk pengenalan wajah (0–1) |
| `ZONE_CONF` | `0.40` | Confidence minimum YOLO untuk deteksi orang |
| `COOLDOWN_SECS` | `20` | Jeda minimum antar event per orang per zona (detik) |

---

## NVR Event Stream

Backend membuka koneksi persistent ke `GET /cgi-bin/eventManager.cgi?action=attach` untuk menerima push event real-time (motion, SMD, dll.).

**Syarat:** User yang digunakan (`DVR_EVENT_USER`) harus punya hak **Remote Alarm** di NVR.

Cara aktifkan di web NVR:
1. Buka `http://10.10.30.2` → login sebagai admin
2. **Configuration → Account** → Edit user `dashboard` (atau buat user baru)
3. Centang **Remote Alarm** (di bawah Permission/Privilege)
4. Simpan → backend akan connect otomatis di retry berikutnya

Jika 403 karena akun terkunci (Dahua RmLock), backend baca durasi lock dari respons JSON dan menunggu tepat `RmLock + 30` detik sebelum retry, dengan exponential backoff maksimum 15 menit.

---

## Struktur File Penting

```
home-dashboard/
├── mediamtx.yml          # Konfigurasi MediaMTX: paths ch1–ch4, runOnInit ffmpeg
├── start_stream.sh       # Script ffmpeg dipanggil MediaMTX per channel
├── start_custom_stream.sh # Script ffmpeg untuk channel custom (tambah lewat UI)
├── docker-compose.yml    # Definisi service: backend, frontend, analyzer
│
├── backend/
│   ├── app.py            # Flask API: kamera, NVR events, zona, wajah, AI events
│   ├── requirements.txt
│   └── Dockerfile
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx    # Halaman utama: CCTV grid, cuaca, smart home
│   │   │   └── ConfigPage.jsx   # Halaman konfigurasi AI (zona, wajah, riwayat)
│   │   ├── features/
│   │   │   ├── cctv/            # CCTVPlayer, AddChannelModal
│   │   │   ├── detection/       # ZoneEditor, FaceManager, EventHistory
│   │   │   ├── smarthome/       # SmartControls, NVREventLog
│   │   │   └── weather/         # WeatherWidget
│   │   └── services/api.js      # Semua fungsi fetch ke backend
│   └── Dockerfile
│
└── analyzer/
    ├── analyzer.py       # Worker AI: YOLO + InsightFace + zona + wajah
    ├── requirements.txt
    └── Dockerfile
```

---

## API Endpoints (Backend :5001)

| Method | Path | Keterangan |
|--------|------|------------|
| GET | `/api/cameras` | Daftar kamera |
| POST | `/api/cameras` | Tambah kamera |
| DELETE | `/api/cameras/:id` | Hapus kamera |
| POST | `/api/cameras/:id/restart` | Restart stream satu channel |
| POST | `/api/cameras/restart-all` | Restart semua stream |
| GET | `/api/stream-status` | Status online/offline tiap channel |
| GET | `/api/nvr-events` | Event NVR real-time (motion, SMD) |
| GET/POST | `/api/zones` | Zona perimeter |
| DELETE/PATCH | `/api/zones/:id` | Edit/hapus zona |
| GET/POST | `/api/faces` | Wajah terdaftar |
| DELETE | `/api/faces/:id` | Hapus wajah |
| GET | `/api/faces/:id/photo` | Foto wajah |
| GET | `/api/cameras/:id/snapshot` | Snapshot terakhir dari analyzer |
| POST | `/api/analyzer-event` | Intake event dari analyzer (internal) |
| GET | `/api/detection-events` | Riwayat event AI (zona/wajah) |
| DELETE | `/api/detection-events` | Hapus semua riwayat |

---

## Troubleshooting

**Website tidak bisa dibuka**
```bash
# Cek container running
docker compose ps

# Restart jika perlu
docker compose down && docker compose up -d
```

**Video tidak muncul (CCTV kosong)**
```bash
# Cek mediamtx jalan di host
pgrep -a mediamtx

# Jalankan jika belum
cd /path/to/home-dashboard/home-dashboard
./mediamtx mediamtx.yml &

# Tes HLS
curl -s http://localhost:8888/ch1/index.m3u8 -o /dev/null -w "%{http_code}\n"
```

**NVR Event Log "Terputus"**
- Pastikan `DVR_EVENT_USER` punya hak Remote Alarm di NVR
- Cek log: `docker compose logs backend | grep NVR`

**Analyzer tidak jalan**
```bash
# Harus pakai profile ai
docker compose --profile ai up -d analyzer
docker compose logs analyzer
```

