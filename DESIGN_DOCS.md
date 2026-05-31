# Home Dashboard — Dokumentasi Desain Sistem

> Versi dokumen: 2.0 · Terakhir diperbarui: 23 Mei 2026  
> Dokumen ini mencerminkan arsitektur **runtime aktual** yang berjalan saat ini.

---

## Daftar Isi

1. [Ringkasan Eksekutif](#1-ringkasan-eksekutif)
2. [Arsitektur Sistem](#2-arsitektur-sistem)
3. [Tech Stack](#3-tech-stack)
4. [Komponen & Layanan](#4-komponen--layanan)
   - 4.1 [Backend (Flask REST API)](#41-backend-flask-rest-api)
   - 4.2 [Frontend (React + Vite)](#42-frontend-react--vite)
   - 4.3 [MediaMTX (Media Server)](#43-mediamtx-media-server)
   - 4.4 [Stream Launcher Bawaan (start_stream.sh)](#44-stream-launcher-bawaan-start_streamsh)
   - 4.5 [Stream Launcher Custom (start_custom_stream.sh)](#45-stream-launcher-custom-start_custom_streamsh)
5. [Alur Data (Data Flow)](#5-alur-data-data-flow)
6. [REST API Reference](#6-rest-api-reference)
7. [Konfigurasi & Environment Variables](#7-konfigurasi--environment-variables)
8. [Panduan Deployment](#8-panduan-deployment)
9. [Audit & Temuan](#9-audit--temuan)
   - 9.1 [Temuan Keamanan (Security)](#91-temuan-keamanan-security)
   - 9.2 [Temuan Arsitektur](#92-temuan-arsitektur)
   - 9.3 [Temuan Kualitas Kode](#93-temuan-kualitas-kode)
10. [Rekomendasi Prioritas](#10-rekomendasi-prioritas)
11. [Roadmap Pengembangan](#11-roadmap-pengembangan)
12. [Dahua NVR — Device Info & Kapabilitas API](#12-dahua-nvr--device-info--kapabilitas-api)
13. [Integrasi Kamera DH-P5AE-PV (Siren/Speaker)](#13-integrasi-kamera-dh-p5ae-pv-sirenspeaker)
14. [NVR Guard Mode (Arm/Disarm)](#14-nvr-guard-mode-armdisarm)
15. [Optimasi MediaMTX — On-Demand Streaming](#15-optimasi-mediamtx--on-demand-streaming)
16. [Optimasi Analyzer — Mode-Aware On-Demand AI](#16-optimasi-analyzer--mode-aware-on-demand-ai)

---

## 1. Ringkasan Eksekutif

**Home Dashboard** adalah aplikasi web berarsitektur microservice yang berjalan di jaringan lokal (LAN) untuk keperluan rumah tangga. Fitur utamanya:

| Fitur | Status | Keterangan |
|---|---|---|
| Live CCTV (4 kanal HLS) | ✅ Berfungsi | Via MediaMTX → HLS.js |
| Manajemen Kanal CCTV | ✅ Berfungsi | Tambah/hapus via REST API |
| Widget Cuaca Real-time | ✅ Berfungsi | Open-Meteo API, hardcoded Jakarta |
| Smart Home Controls | ⚠ UI-only | Tidak terhubung device fisik |
| Otentikasi Pengguna | ❌ Tidak ada | Risiko keamanan tinggi |

---

## 2. Arsitektur Sistem

### 2.1 Diagram Komponen

```
┌──────────────────────────────────────────────────────────────────┐
│                        JARINGAN LOKAL (LAN)                      │
│                                                                  │
│  ┌──────────────────┐  RTSPS/554   ┌──────────────────────────┐  │
│  │   Dahua NVR      │◄─────────────│   start_stream.sh        │  │
│  │  DHI-NVR4108HS   │  (4 kanal)   │   (ffmpeg pull per ch)   │  │
│  │  10.10.30.2:554  │              └───────────┬──────────────┘  │
│  │  10.10.30.2:80   │                          │ ffmpeg push      │
│  └──────────────────┘                          │ rtsp://localhost:8554/chN
│                                      ┌─────────▼──────────┐      │
│                                      │     MediaMTX        │      │
│                                      │  RTSP :8554         │      │
│                                      │  HLS  :8888         │      │
│                                      │  runOnInit ch1–ch4  │      │
│                                      │  regex custom_*     │      │
│                                      └─────────┬──────────┘      │
│                                                │ HLS (m3u8+ts)    │
│               REST API /api/cameras            │                  │
│  ┌───────────────────────┐          ┌──────────▼──────────┐      │
│  │  Backend Flask        │          │  Frontend React      │      │
│  │  :5001 (host)         │◄─────────│  :5173 (Vite dev)   │      │
│  │  :5000 (container)    │  JSON    │  HLS.js player       │      │
│  │  SQLite /data/cameras │          │                      │      │
│  └───────────────────────┘          └──────────────────────┘      │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │ Docker bridge network: hd-net                           │     │
│  │  - backend container  (service: backend)                │     │
│  │  - frontend container (service: frontend)               │     │
│  │  ⚠ MediaMTX TIDAK ada di docker-compose (lihat §9.2)   │     │
│  └─────────────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Diagram Alur Streaming

```
                  MediaMTX start (./mediamtx mediamtx.yml)
                       │
                       │ runOnInit (per path ch1–ch4)
                       ▼
               start_stream.sh <N>
                       │
               ffmpeg -rtsp_transport tcp -tls_verify 0
                       │ pull RTSPS dari DVR
                       ▼
           rtsps://dashboard:***@10.10.30.2:554/cam/realmonitor?channel=N&subtype=1
                       │
               -c:v h264_videotoolbox -b:v 800k
                       │ RTSP push
                       ▼
                   MediaMTX (:8554/chN)
                       │ remux → HLS segments (.ts)
                       ▼
               MediaMTX HLS server (:8888)
                /ch1/index.m3u8  /ch2/index.m3u8
                /ch3/index.m3u8  /ch4/index.m3u8
                       │
              Browser HLS.js ◄─── polling manifest
```

**Alur kamera custom (tambah via modal):**

```
POST /api/cameras {name, rtsp_url, channel}
       │
       └── backend: uuid → custom_<10hex> path_name
                      │
              start_custom_stream.sh <rtsp_url> <path_name>
                      │ ffmpeg push
                      ▼
              MediaMTX regex path ~^custom_[0-9a-f]+$
                      │
              HLS: http://localhost:8888/custom_<id>/index.m3u8
```

### 2.3 Port Mapping

| Port (Host) | Port (Container) | Layanan | Protokol |
|---|---|---|---|
| 5001 | 5000 | Flask Backend | HTTP |
| 5173 | 5173 | Vite Frontend | HTTP |
| 8888 | - | MediaMTX HLS | HTTP (external) |
| 8554 | - | MediaMTX RTSP | RTSP (internal) |
| 554 | - | Dahua DVR RTSP | RTSPS |
| 80 | - | Nginx (legacy HLS) | HTTP |

---

## 3. Tech Stack

| Layer | Teknologi | Versi | Keterangan |
|---|---|---|---|
| Backend | Python / Flask | 2.3.2 | REST API |
| ORM/DB | SQLite | built-in | Penyimpanan data kamera |
| WSGI | Gunicorn | 20.1.0 | Production server |
| CORS | flask-cors | 3.0.10 | Cross-origin header |
| Frontend | React | 18.2.0 | UI framework |
| Build Tool | Vite | 5.0.0 | Dev server + bundler |
| HLS Player | hls.js | ≥1.4.0 | Browser HLS playback |
| Media Server | MediaMTX | v1.16.2 | RTSP↔HLS bridge |
| Stream Tool | ffmpeg | latest | Transcoding RTSP |
| ONVIF Client | onvif-zeep | latest | DVR discovery |
| Container | Docker / Compose | v2 | Deployment |
| Reverse Proxy | Nginx | latest | Legacy static HLS |
| Weather API | Open-Meteo | v1 | Cuaca gratis (no key) |

---

## 4. Komponen & Layanan

### 4.1 Backend (Flask REST API)

**File:** `backend/app.py`  
**Port:** 5000 (container) / 5001 (host)

#### Tanggung Jawab
- Menyimpan daftar kamera (nama + HLS URL) di SQLite
- Menyediakan REST API CRUD untuk manajemen kamera
- Seed 4 kamera default saat database pertama kali dibuat

#### Database Schema

```sql
CREATE TABLE cameras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    stream_url TEXT    NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    builtin    INTEGER NOT NULL DEFAULT 0,  -- 1 = kamera bawaan (tidak bisa dihapus)
    rtsp_url   TEXT,                        -- RTSP sumber untuk kamera custom
    channel    INTEGER                      -- Nomor channel DVR untuk kamera custom
);
```

#### Environment Variables

| Variable | Default | Keterangan |
|---|---|---|
| `BASE_URL` | `http://localhost:8888` | Base URL MediaMTX untuk seed URL kamera |
| `DB_PATH` | `/data/cameras.db` | Path SQLite database |
| `PORT` | `5000` | Port Flask / Gunicorn |

#### Catatan Implementasi
- Koneksi DB dibuka per-request menggunakan Flask `g` context dan ditutup di `teardown_appcontext`
- `init_db()` dipanggil **sekali** di level modul (saat `app.py` diimport) — berlaku untuk dev-run maupun Gunicorn
- `ensure_camera_columns()` menambahkan kolom `rtsp_url` dan `channel` secara idempoten jika belum ada (backward-compatible migration)
- `restore_custom_streams()` dipanggil saat startup untuk me-launch ulang ffmpeg untuk kamera custom yang tersimpan di DB

---

### 4.2 Frontend (React + Vite)

**File:** `frontend/src/`  
**Port:** 5173

#### Struktur Komponen

```
App.jsx
└── Dashboard.jsx (page)
    ├── Clock (inline component)  — jam real-time setiap detik
    ├── WeatherWidget.jsx          — widget cuaca Open-Meteo
    ├── SmartControls.jsx          — tombol lampu + gate
    ├── CCTVPlayer.jsx × N         — player HLS per kamera
    └── AddChannelModal.jsx        — modal tambah kamera
```

#### Alur State (Dashboard.jsx)

```
useEffect → fetchCameras() → setCams([...])
                                    │
                           render CCTVPlayer × N
                                    │
handleAdd → addCamera() → reload()  │
handleRemove → deleteCamera() → setCams.filter()
```

#### CCTVPlayer — State Machine

```
[loading] ──MANIFEST_PARSED──► [live]
    │                              │
    │◄──────404 (8s retry)─────────┤
    │                              │
    └──MEDIA_ERROR──► recoverMediaError()
    └──OTHER_FATAL──► destroy + setTimeout(attach, 4s)
```

**Fitur:**
- Mute/unmute toggle
- Fullscreen API
- Auto-retry dengan exponential backoff pada error 404 (stream belum live)
- Native Safari HLS fallback (`video.canPlayType`)
- Indikator status: loading (kuning), live (hijau), error (merah)

#### SmartControls — State

```
localStorage['hd_lights'] = [{id, name, icon, on}, ...]
localStorage['hd_gate']   = 'open' | 'closed'
```

> **Catatan:** State Smart Home sepenuhnya ada di localStorage browser. Tidak ada backend atau integrasi IoT nyata.

---

### 4.3 MediaMTX (Media Server)

**File:** `mediamtx.yml`  
**Binary:** `home-dashboard/mediamtx` (ARM64 Darwin, v1.16.2)

#### Konfigurasi Aktual

```yaml
logLevel: info
readTimeout: 30s
writeTimeout: 30s

hlsSegmentDuration: 4s
hlsSegmentMaxSize: 50MB
hlsAllowOrigins:
  - "*"
hlsAlwaysRemux: yes

paths:
  ch1:
    runOnInit: /path/to/start_stream.sh 1   # MediaMTX spawn ffmpeg otomatis
    runOnInitRestart: yes                    # Auto-restart jika ffmpeg crash

  ch2:
    runOnInit: /path/to/start_stream.sh 2
    runOnInitRestart: yes

  ch3:
    runOnInit: /path/to/start_stream.sh 3
    runOnInitRestart: yes

  ch4:
    runOnInit: /path/to/start_stream.sh 4
    runOnInitRestart: yes

  '~^custom_[0-9a-f]+$': {}   # Menerima push dari start_custom_stream.sh
```

**Cara kerja:**
1. Saat MediaMTX start, setiap path `ch1`–`ch4` memicu `runOnInit` → spawn `start_stream.sh N`
2. `start_stream.sh` menjalankan ffmpeg yang menarik RTSPS dari DVR dan push ke `rtsp://localhost:8554/chN`
3. `runOnInitRestart: yes` memastikan ffmpeg di-respawn otomatis jika crash — tanpa perlu supervisord
4. Path custom menggunakan regex `~^custom_[0-9a-f]+$` — menerima push dari backend via `start_custom_stream.sh`
5. Browser mengakses HLS di `http://localhost:8888/chN/index.m3u8`

> ⚠ **MediaMTX tidak dimasukkan ke `docker-compose.yml`** — harus dijalankan manual di host.

---

### 4.4 Stream Launcher Bawaan (start_stream.sh)

**File:** `start_stream.sh`

Script shell yang di-spawn oleh MediaMTX via `runOnInit` untuk setiap channel bawaan (ch1–ch4).

#### Kode

```sh
#!/bin/sh
CH=$1
DVR_HOST=10.10.30.2
DVR_USER=dashboard
DVR_PASS='d4$hb0ard-dlt'   # single-quoted: $ literal dalam assignment

exec /opt/homebrew/bin/ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp -tls_verify 0 \
  -i "rtsps://${DVR_USER}:${DVR_PASS}@${DVR_HOST}:554/cam/realmonitor?channel=${CH}&subtype=1&unicast=true&proto=Onvif&tls=true" \
  -c:v h264_videotoolbox -b:v 800k -profile:v main \
  -c:a aac -b:a 64k -af aresample=async=1 \
  -f rtsp rtsp://localhost:8554/ch${CH}
```

> **Mengapa credentials di shell script dan bukan di `mediamtx.yml`?**  
> MediaMTX mengekspansi `$VAR` sebelum passing ke shell sehingga `d4$hb0ard-dlt` akan terpotong di `d4`. Menyimpan password di script dengan single-quotes menghindari masalah ini.

> ⚠ `h264_videotoolbox` adalah encoder khusus macOS/Apple Silicon. Tidak bisa dipakai di Docker/Linux.

---

### 4.5 Stream Launcher Custom (start_custom_stream.sh)

**File:** `start_custom_stream.sh`

Script shell yang di-spawn oleh backend (`app.py`) saat user menambahkan kamera custom via `AddChannelModal`.

#### Cara Kerja

```
POST /api/cameras {name, rtsp_url, channel}
       │
       └── backend: generate path_name = "custom_<uuid10hex>"
               │     stream_url = http://localhost:8888/custom_<id>/index.m3u8
               │     Simpan ke DB
               │
               └── subprocess.Popen([start_custom_stream.sh, rtsp_url, path_name])
```

#### Kode

```sh
#!/bin/sh
SOURCE_URL=$1
PATH_NAME=$2
RTSP_PORT_VALUE=${RTSP_PORT:-8554}

exec /opt/homebrew/bin/ffmpeg \
  -hide_banner -loglevel warning \
  -rtsp_transport tcp -tls_verify 0 \
  -i "$SOURCE_URL" \
  -c:v h264_videotoolbox -b:v 800k -profile:v main \
  -c:a aac -b:a 64k -af aresample=async=1 \
  -f rtsp "rtsp://localhost:${RTSP_PORT_VALUE}/${PATH_NAME}"
```

Backend (`app.py`) menyimpan `subprocess.Popen` handle di dict `CUSTOM_STREAM_PROCS[path_name]`. Stream dihentikan otomatis saat kamera dihapus atau backend shutdown (`atexit` handler).

---

## 5. Alur Data (Data Flow)

### 5.1 Inisialisasi Dashboard

```
Browser buka http://localhost:5173
    │
    ├── Vite proxy /api/* → http://localhost:5001
    │
    ├── GET /api/cameras
    │   └── Flask → SQLite → JSON [{id, name, stream_url, builtin}]
    │
    ├── render CCTVPlayer(src="http://localhost:8888/ch1/index.m3u8")
    │   └── hls.js loadSource() → GET /ch1/index.m3u8 → MediaMTX
    │
    ├── WeatherWidget
    │   └── fetch https://api.open-meteo.com/v1/forecast?...
    │
    └── SmartControls
        └── localStorage.getItem('hd_lights')
```

### 5.2 Tambah Kamera Baru

Ada dua mode tambah kamera:

**Mode A — URL langsung (stream sudah ada):**
```
User isi: nama + stream_url
    └── POST /api/cameras {name, stream_url}
            └── Flask INSERT INTO cameras
                    └── reload() → render CCTVPlayer baru
```

**Mode B — RTSP custom (URL DVR + nomor channel):**
```
User isi: nama + rtsp_url + channel
    └── POST /api/cameras {name, rtsp_url, channel}
            │
            └── Flask:
                  1. generate path_name = "custom_<uuid10hex>"
                  2. stream_url = http://localhost:8888/custom_<id>/index.m3u8
                  3. INSERT INTO cameras (name, stream_url, rtsp_url, channel)
                  4. launch_custom_stream() → Popen(start_custom_stream.sh, rtsp_url, path_name)
                  │
                  └── ffmpeg pull RTSPS → push ke MediaMTX /custom_<id>
                          └── HLS tersedia → CCTVPlayer.jsx memutar stream
```

### 5.3 Refresh Cuaca

Cuaca di-fetch sekali saat komponen mount (`useEffect([], [])`). Tidak ada refresh otomatis (interval). Data yang diambil:
- `current_weather`: suhu, kecepatan angin, arah angin, kode cuaca WMO
- `hourly`: kelembaban relatif dan apparent temperature jam ke-N (jam sekarang)

---

## 6. REST API Reference

Base URL: `http://localhost:5001`  
Content-Type: `application/json`

### GET /api/cameras

Ambil semua kamera, diurutkan berdasarkan `sort_order`.

**Response 200:**
```json
[
  {
    "id": 1,
    "name": "Camera 1",
    "stream_url": "http://localhost:8888/ch1/index.m3u8",
    "builtin": 1
  }
]
```

---

### POST /api/cameras

Tambah kamera baru. Mendukung dua mode:

**Mode A — stream URL langsung:**
```json
{
  "name": "Camera Belakang",
  "stream_url": "http://localhost:8888/ch5/index.m3u8"
}
```

**Mode B — RTSP custom (backend launch ffmpeg):**
```json
{
  "name": "Camera Belakang",
  "rtsp_url": "rtsps://user:pass@192.168.1.100:554/stream",
  "channel": 1
}
```
Jika `rtsp_url` + `channel` diberikan, `stream_url` di response berupa URL HLS yang digenerate otomatis (`http://localhost:8888/custom_<id>/index.m3u8`).

**Response 201:**
```json
{
  "id": 5,
  "name": "Camera Belakang",
  "stream_url": "http://localhost:8888/custom_a3f9c12b1d/index.m3u8",
  "builtin": 0
}
```

**Validasi:**
- `name`: wajib, tidak boleh kosong
- `stream_url`: wajib jika mode A, harus diawali `http`
- `rtsp_url`: wajib jika mode B, harus punya scheme rtsp/rtsps
- `channel`: wajib jika mode B, harus integer positif

**Error 400:**
```json
{ "error": "name is required" }
{ "error": "stream_url must start with http" }
{ "error": "rtsp_url is required" }
{ "error": "channel must be a positive integer" }
```

---

### DELETE /api/cameras/:id

Hapus kamera. Kamera `builtin=1` tidak bisa dihapus. Jika kamera custom, proses ffmpeg-nya juga dihentikan.

**Response 204:** No content (berhasil)

**Error 403:**
```json
{ "error": "cannot delete built-in camera" }
```

**Error 404:**
```json
{ "error": "not found" }
```

---

### PATCH /api/cameras/:id

Update nama dan/atau URL kamera.

**Request Body (semua field opsional):**
```json
{
  "name": "Nama Baru",
  "stream_url": "http://localhost:8888/ch1/index.m3u8"
}
```

**Response 200:** Data kamera yang diupdate.

---

### GET /api/stream-status

Cek status online/offline semua stream secara paralel (HTTP HEAD ke tiap `stream_url`, timeout 3s).

**Response 200:**
```json
[
  { "id": 1, "name": "Camera 1", "online": true,  "builtin": true  },
  { "id": 5, "name": "Belakang", "online": false, "builtin": false }
]
```

---

### POST /api/stream-restart/:id

Restart stream untuk kamera tertentu:
- Kamera builtin: `pkill` ffmpeg yang push ke path tersebut → MediaMTX `runOnInitRestart` akan respawn
- Kamera custom: `stop_custom_stream()` + `launch_custom_stream()` ulang

**Response 200:**
```json
{ "ok": true }
```

---

### POST /api/stream-restart-all

Restart semua stream (builtin dan custom). Sama dengan memanggil restart per kamera untuk semua entri di DB.

**Response 200:**
```json
{ "ok": true }
```

---

## 7. Konfigurasi & Environment Variables

### 7.1 Backend (docker-compose.yml)

```yaml
environment:
  - BASE_URL=http://localhost:8888   # URL MediaMTX untuk generate stream URL default
  - DB_PATH=/data/cameras.db         # Path database SQLite
```

> ⚠ `BASE_URL=http://localhost:8888` tidak tepat di dalam Docker. Browser mengakses dari host, bukan dari dalam container. Lihat §9.2 #5.

### 7.2 Frontend (vite.config.js)

```js
proxy: {
  '/api': {
    target: 'http://localhost:5001',  // Harus cocok dengan port host backend
  }
}
```

### 7.3 MediaMTX (mediamtx.yml)

File dikonfigurasi dengan `runOnInit` per channel — MediaMTX **secara otomatis** spawn dan restart ffmpeg saat start. Tidak perlu `onvif_stream.py` atau script eksternal apapun selain `start_stream.sh`.

Path `ch1`–`ch4` memiliki `runOnInit` + `runOnInitRestart: yes`. Path custom menggunakan regex `~^custom_[0-9a-f]+$` tanpa `runOnInit` karena di-push oleh backend.

### 7.4 Stream Scripts

**`start_stream.sh`** — credentials DVR ada di sini (bukan di `mediamtx.yml`) untuk menghindari masalah ekspansi `$VAR` oleh MediaMTX:

```sh
DVR_HOST=10.10.30.2
DVR_USER=dashboard
DVR_PASS='d4$hb0ard-dlt'   # ⚠ KREDENSIAL TERBUKA DI FILE
```

**`start_custom_stream.sh`** — menerima `SOURCE_URL` dan `PATH_NAME` sebagai argumen dari backend. Membaca `RTSP_PORT` dari environment (default 8554).

---

## 8. Panduan Deployment

### 8.1 Mode Development (Lokal tanpa Docker)

#### Prasyarat
- Python 3.11+
- Node.js 18+
- ffmpeg (macOS: `brew install ffmpeg`)
- MediaMTX binary (sudah ada di `home-dashboard/mediamtx`)
- `onvif-zeep` untuk eksplorasi API (opsional): `pip3 install onvif-zeep --break-system-packages`

#### Langkah

```bash
# 1. Jalankan MediaMTX
#    MediaMTX otomatis spawn ffmpeg untuk ch1–ch4 via runOnInit
cd home-dashboard
./mediamtx mediamtx.yml

# 2. Jalankan Backend
cd backend
pip3 install -r requirements.txt
PORT=5001 DB_PATH=/tmp/cameras.db BASE_URL=http://localhost:8888 python3 app.py

# 3. Jalankan Frontend
cd frontend
npm install
npm run dev
# Buka http://localhost:5173
```

> Stream bawaan (ch1–ch4) akan otomatis aktif saat MediaMTX start — tidak perlu langkah tambahan selama DVR `10.10.30.2` reachable.

### 8.2 Mode Docker

```bash
cd home-dashboard
docker compose up --build
```

> ⚠ MediaMTX harus tetap dijalankan manual di host karena tidak ada di `docker-compose.yml`.

**Akses:**
- Frontend: `http://localhost:5173`
- Backend API: `http://localhost:5001/api/cameras`
- HLS Stream: `http://localhost:8888/ch1/index.m3u8`

### 8.3 Test Konektivitas DVR

```bash
# Test TCP ke DVR
nc -zv -w 3 10.10.30.2 554

# Test HLS stream (harus aktif ~5s setelah MediaMTX start)
curl -s http://localhost:8888/ch1/index.m3u8 | head -5

# Eksplorasi API Dahua NVR (lihat §12)
python3 dahua_explore.py --probe
python3 dahua_explore.py --events --event-duration 30
python3 dahua_explore.py --recordings
```

---

## 9. Audit & Temuan

### 9.1 Temuan Keamanan (Security)

> Tingkat keparahan: 🔴 Kritis · 🟠 Tinggi · 🟡 Sedang · 🟢 Rendah

---

#### [SEC-01] 🔴 KRITIS — Kredensial DVR Hardcoded di Source Code

**File:** `onvif_stream.py` baris 25–27, `dahua_stream_proxy.py` baris 17–19

```python
# KONDISI SAAT INI (BERBAHAYA):
HOST     = "10.10.30.2"
USERNAME = "dashboard"
PASSWORD = "d4$hb0ard-dlt"
```

**Risiko:** Siapapun yang mengakses repository (GitHub, leak, dll) mendapat full akses ke DVR dan semua feed kamera CCTV.

**Referensi OWASP:** A07:2021 – Identification and Authentication Failures

**Rekomendasi:**
```python
# Ganti dengan environment variable:
import os
HOST     = os.environ["DVR_HOST"]
USERNAME = os.environ["DVR_USER"]
PASSWORD = os.environ["DVR_PASS"]
```
Simpan nilai di file `.env` dan tambahkan `.env` ke `.gitignore`.

---

#### [SEC-02] 🔴 KRITIS — Tidak Ada Otentikasi pada Dashboard

**File:** Semua route di `backend/app.py`, `frontend/`

Dashboard dapat diakses langsung tanpa login. Siapapun di jaringan lokal bisa:
- Melihat semua feed CCTV live
- Menambah/menghapus kamera
- Melihat/mengontrol status Smart Home

**Rekomendasi:** Implementasikan minimal HTTP Basic Auth di nginx reverse proxy, atau tambahkan session-based auth di Flask.

---

#### [SEC-03] 🟠 TINGGI — CORS Terbuka Sepenuhnya

**File:** `backend/app.py` baris 7, `mediamtx.yml` baris 11

```python
CORS(app)  # Mengizinkan semua origin
```
```yaml
hlsAllowOrigin: "*"  # HLS stream bisa diakses dari origin manapun
```

**Risiko:** Endpoint API dan HLS stream bisa diakses oleh halaman web manapun (CSRF/data harvesting).

**Rekomendasi:**
```python
CORS(app, origins=["http://localhost:5173", "http://192.168.x.x:5173"])
```

---

#### [SEC-04] 🟡 SEDANG — Validasi `stream_url` Terlalu Lemah

**File:** `backend/app.py` baris 74 dan 117

```python
if not stream_url.startswith('http'):   # Terlalu permisif
```

**Risiko:** User bisa input URL berbahaya seperti `http://internal-service/admin`, memungkinkan SSRF (Server-Side Request Forgery) jika backend melakukan request ke URL tersebut. Saat ini backend hanya menyimpan URL, namun risiko meningkat jika validasi tidak diperketat.

**Rekomendasi:**
```python
from urllib.parse import urlparse
parsed = urlparse(stream_url)
if parsed.scheme not in ('http', 'https') or not parsed.netloc:
    return jsonify({'error': 'Invalid URL format'}), 400
```

---

#### [SEC-05] 🟢 RENDAH — Nginx `autoindex on` Mengekspos Direktori

**File:** `nginx/default.conf` baris 7

```nginx
autoindex on;  # Menampilkan listing isi direktori
```

**Risiko:** Mengekspos struktur direktori dan file `.ts` (rekaman video) ke semua pengguna.

**Rekomendasi:** Ganti `autoindex on` dengan `autoindex off`.

---

#### [SEC-06] 🟢 RENDAH — Tidak Ada HTTPS

Semua komunikasi (API, HLS stream, dashboard) menggunakan plain HTTP. Pada jaringan rumah risikonya minimal, namun tetap praktik buruk.

**Rekomendasi:** Gunakan self-signed TLS certificate via nginx reverse proxy untuk akses lokal.

---

### 9.2 Temuan Arsitektur

---

#### [ARCH-01] ⚠️ MediaMTX Tidak Ada di docker-compose.yml

**File:** `docker-compose.yml`

MediaMTX adalah komponen inti sistem (RTSP→HLS bridge) namun tidak didefinisikan sebagai Docker service. Ini menyebabkan:
- Setup tidak sepenuhnya reproducible (harus jalankan binary manual)
- Tidak ada restart policy jika MediaMTX crash
- Dokumentasi menyesatkan ("docker compose up --build" tidak menghasilkan sistem yang berfungsi penuh)

**Rekomendasi:** Tambahkan service `mediamtx` ke `docker-compose.yml`:
```yaml
  mediamtx:
    image: bluenviron/mediamtx:latest
    ports:
      - "8554:8554"
      - "8888:8888"
    volumes:
      - ./mediamtx.yml:/mediamtx.yml:ro
    networks:
      - hd-net
```

---

#### [ARCH-02] ⚠️ Frontend Dockerfile Menggunakan Vite Dev Server di "Production"

**File:** `frontend/Dockerfile`

```dockerfile
CMD ["npm", "run", "dev"]   # Vite dev server, BUKAN production build
```

Dev server Vite:
- Tidak dioptimalkan untuk produksi
- Lebih lambat (HMR overhead)
- Tidak ter-minify / tidak di-bundle

**Rekomendasi:** Gunakan multi-stage build:
```dockerfile
FROM node:18 AS builder
WORKDIR /app
COPY package*.json ./
RUN npm install --legacy-peer-deps
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
```

---

#### [ARCH-03] ⚠️ Orphaned Stage di Backend Dockerfile

**File:** `backend/Dockerfile` baris 1

```dockerfile
FROM node:18 as installer   # Stage ini tidak pernah digunakan!
FROM python:3.11-slim
```

Stage `installer` tidak melakukan apa-apa dan tidak di-COPY ke stage berikutnya. Docker tetap mem-pull image `node:18` tanpa perlu.

**Rekomendasi:** Hapus baris `FROM node:18 as installer`.

---

#### [ARCH-04] ⚠️ BASE_URL di docker-compose.yml Salah untuk Konteks Browser

**File:** `docker-compose.yml` baris 7

```yaml
- BASE_URL=http://localhost:8888
```

URL ini digunakan sebagai prefix `stream_url` di database. Namun:
- Di dalam Docker, `localhost` merujuk ke container itu sendiri
- Browser client mengakses dari host → URL yang tepat bergantung pada IP host

Hasil: kamera bawaan (seed) memiliki URL yang benar dari perspektif browser (karena browser dan MediaMTX sama-sama di host), namun ini tidak akan bekerja jika di-deploy ke server remote.

**Rekomendasi:** Pisahkan `BASE_URL` untuk seed (internal) dan URL yang dikembalikan ke client (publik). Atau gunakan relative URL dan route HLS melalui backend proxy.

---

#### [ARCH-05] ⚠️ Smart Home Hanya Menyimpan State di localStorage

**File:** `frontend/src/features/smarthome/SmartControls.jsx`

State lampu dan gate tidak tersinkronisasi antar session/device dan tidak terhubung ke hardware nyata (tidak ada MQTT, tidak ada HTTP ke relay controller).

**Rekomendasi jangka pendek:** Tambahkan endpoint backend `/api/smarthome/lights` dan `/api/smarthome/gate` untuk persistensi.  
**Rekomendasi jangka panjang:** Integrasikan dengan Home Assistant, MQTT, atau GPIO controller.

---

#### [ARCH-06] ⚠️ `ffmpeg` Path Hardcoded ke macOS (`/opt/homebrew/bin/ffmpeg`)

**File:** `onvif_stream.py` baris 37

```python
FFMPEG = "/opt/homebrew/bin/ffmpeg"
```

Tidak bisa dijalankan di Linux/Docker.

**Rekomendasi:**
```python
import shutil
FFMPEG = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
```

---

#### [ARCH-07] ⚠️ Cuaca Hardcoded Jakarta, Tidak Bisa Dikonfigurasi

**File:** `frontend/src/features/weather/WeatherWidget.jsx` baris 36–38

```js
const DEFAULT_LAT  = -6.2088
const DEFAULT_LON  = 106.8456
const DEFAULT_CITY = 'Jakarta'
```

Dan tidak ada refresh interval — cuaca hanya dimuat sekali saat halaman pertama dibuka.

**Rekomendasi:** Pindahkan konfigurasi lokasi ke environment variable atau pengaturan user. Tambahkan interval refresh setiap 15 menit.

---

#### [ARCH-08] ⚠️ `AddChannelModal` Hardcoded `localhost:8888`

**File:** `frontend/src/features/cctv/AddChannelModal.jsx` baris 3

```js
const BASE = 'http://localhost:8888'
```

Nilai ini muncul sebagai placeholder/hint di modal. Jika aplikasi diakses dari perangkat lain di LAN, URL suggestion menjadi salah.

**Rekomendasi:** Baca dari `import.meta.env.VITE_MEDIAMTX_URL` dengan fallback ke `http://localhost:8888`.

---

#### [ARCH-09] ⚠️ Tidak Ada Health Check / Readiness Probe

`docker-compose.yml` tidak memiliki `healthcheck` untuk backend atau frontend. Service `frontend` hanya menunggu `depends_on: backend` tanpa memastikan backend benar-benar siap menerima request.

**Rekomendasi:**
```yaml
backend:
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:5000/api/cameras"]
    interval: 10s
    timeout: 5s
    retries: 3
```

---

### 9.3 Temuan Kualitas Kode

---

#### [CODE-01] `init_db()` — Side Effect Saat Import

**File:** `backend/app.py` (baris terakhir sebelum `if __name__`)

```python
init_db()   # Dipanggil di level modul — berlaku untuk dev-run dan Gunicorn
```

`init_db()` membuka koneksi DB, membuat tabel, dan mengeksekusi `restore_custom_streams()` saat modul pertama kali diimport. Ini menyebabkan side effect saat unit testing — `import app` langsung mengeksekusi DB operation.

**Catatan:** Versi lama memanggil `init_db()` dua kali (sekali di level modul, sekali di `if __name__ == '__main__'`). Kondisi saat ini sudah diperbaiki — hanya dipanggil **sekali** di level modul.

**Rekomendasi:** Untuk testability yang lebih baik, pindahkan ke Gunicorn hook:
```python
# gunicorn.conf.py
def on_starting(server):
    from app import init_db
    init_db()
```

# Called by gunicorn
init_db()                      # Dipanggil saat modul di-import oleh Gunicorn
```

Pemanggilan `init_db()` di level modul menyebabkan side effect saat import (membuka koneksi DB, membuat file, dll). Ini berpengaruh pada unit testing — `import app` langsung mengeksekusi DB operation.

**Rekomendasi:** Hapus pemanggilan `init_db()` di level modul dan gantikan dengan Gunicorn hook:
```python
# gunicorn.conf.py
def on_starting(server):
    from app import init_db
    init_db()
```

---

#### [CODE-02] Tidak Ada File `.env.example`

Tidak ada dokumentasi tentang environment variable yang diperlukan. Developer baru tidak tahu variabel apa yang harus diset.

**Rekomendasi:** Buat `.env.example`:
```env
DVR_HOST=10.10.30.2
DVR_USER=dashboard
DVR_PASS=changeme
BASE_URL=http://localhost:8888
DB_PATH=/data/cameras.db
VITE_BACKEND_URL=
VITE_MEDIAMTX_URL=http://localhost:8888
```

---

#### [CODE-03] Tidak Ada Penanganan Error di `fetchCameras`

**File:** `frontend/src/pages/Dashboard.jsx` baris 27

```js
const reload = () => fetchCameras().then(setCams).catch(e => setError(e.message))
```

Error hanya ditampilkan teks, tidak ada tombol "Coba Lagi". Jika backend down, user harus refresh halaman manual.

---

#### [CODE-04] Tidak Ada File `.gitignore` Lengkap

Dari konteks proyek, beberapa file berpotensi ter-commit ke git:
- `hls/*.ts` (file video rekaman)
- `mediamtx` (binary 50MB+)
- `node_modules/`
- `*.db`

---

#### [CODE-05] Mixed Language (Indonesia + English) di Codebase

Komentar kode dan label UI sebagian dalam Bahasa Indonesia, sebagian Inggris. Contoh: error message API dalam Inggris (`"name is required"`), sedangkan UI dalam Bahasa Indonesia (`"Menyimpan..."`).

---

#### [CODE-06] `CCTVPlayer` Tidak Menampilkan Detail Error

Saat fatal error terjadi, hanya `status='error'` yang di-set namun pesan error tidak ditampilkan ke user. User tidak tahu apakah error karena stream belum aktif atau DVR offline.

---

## 10. Rekomendasi Prioritas

### Prioritas 1 — Segera (Security Critical)

| # | Aksi | File | Estimasi |
|---|---|---|---|
| P1-1 | Pindahkan kredensial DVR ke environment variable / `.env` | `onvif_stream.py`, `dahua_stream_proxy.py` | 30 menit |
| P1-2 | Tambahkan basic authentication di dashboard | `backend/app.py` / nginx | 2-4 jam |
| P1-3 | Restrict CORS ke origin yang diizinkan | `backend/app.py`, `mediamtx.yml` | 15 menit |
| P1-4 | Nonaktifkan `autoindex` di nginx | `nginx/default.conf` | 5 menit |

### Prioritas 2 — Jangka Pendek (Arsitektur)

| # | Aksi | File | Estimasi |
|---|---|---|---|
| P2-1 | Tambahkan MediaMTX ke `docker-compose.yml` | `docker-compose.yml` | 30 menit |
| P2-2 | Perbaiki `frontend/Dockerfile` gunakan production build | `frontend/Dockerfile` | 1 jam |
| P2-3 | Hapus orphaned `FROM node:18` di backend Dockerfile | `backend/Dockerfile` | 5 menit |
| P2-4 | Fix `FFMPEG` path dinamis | `onvif_stream.py` | 10 menit |
| P2-5 | Tambahkan health check ke docker-compose | `docker-compose.yml` | 30 menit |
| P2-6 | Buat file `.env.example` dan update `.gitignore` | root | 20 menit |

### Prioritas 3 — Jangka Menengah (Fitur & Kualitas)

| # | Aksi | Keterangan |
|---|---|---|
| P3-1 | Backend persistence untuk Smart Home state | Endpoint `/api/smarthome` |
| P3-2 | Konfigurasi lokasi cuaca dinamis | Dari env / pengaturan user |
| P3-3 | Tombol retry saat backend error | Dashboard.jsx |
| P3-4 | Perbaiki `BASE_URL` di docker-compose untuk konteks browser | Dokumentasi atau refactor |
| P3-5 | Tambahkan refresh interval cuaca (15 menit) | WeatherWidget.jsx |
| P3-6 | Perbaiki pesan error CCTVPlayer lebih detail | CCTVPlayer.jsx |

---

## 11. Roadmap Pengembangan

Berdasarkan README proyek dan kondisi kode saat ini:

### Fase 1 — Stabilisasi (1-2 minggu)
- [ ] Implementasi semua Prioritas 1 (keamanan)
- [ ] Semua service berjalan via `docker compose up` tanpa setup manual
- [ ] Frontend build production (bukan dev server)

### Fase 2 — Smart Home Integration (2-4 minggu)
- [ ] Backend API untuk Smart Home state
- [ ] Integrasi MQTT atau Home Assistant REST API
- [ ] Notifikasi push (Web Push API) untuk event kamera / sensor

### Fase 3 — Fitur Lanjutan (1-2 bulan)
- [ ] ONVIF device discovery otomatis (scan LAN)
- [ ] Multi-camera layout presets (2×2, 3×1, fullscreen)
- [ ] Perekaman terjadwal + manajemen storage
- [ ] Motion detection sederhana (ffmpeg motion filter)
- [ ] Autentikasi pengguna + multi-user role

### Fase 4 — AI & Analitik (roadmap)
- [ ] Object detection (YOLO / ONNX inference di backend)
- [ ] Timeline event (alert log per kamera)
- [ ] Dashboard statistik (uptime, event summary)

---

## 12. Dahua NVR — Device Info & Kapabilitas API

> Hasil eksplorasi menggunakan `dahua_explore.py` pada 23 Mei 2026.  
> NVR dapat diakses di `http://10.10.30.2` dengan Digest Auth (bukan Basic Auth).

### 12.1 Informasi Device

| Field | Nilai |
|---|---|
| Model | DHI-NVR4108HS-4KS3 |
| Hardware Version | V1.0 |
| Firmware | 4.006.0000000.0.R, build 2025-04-02 |
| Serial Number | BG0579CPAJ920FE |
| Hostname | NVR |
| IP eth0 (aktif) | 10.10.30.2 / 255.255.255.0 |
| IP bond0 | 192.168.1.109 / 255.255.255.0 |
| Default GW (eth0) | 10.10.30.1 |
| DNS | 10.10.30.1 |

### 12.2 Channel / Kamera

NVR memiliki **8 channel slot**. Berdasarkan konfigurasi yang terbaca:

| Channel | Nama | Status |
|---|---|---|
| 1 | Channel1 | Aktif (SmartMotion enabled) |
| 2 | IPC | Aktif (SmartMotion enabled) |
| 3 | IPC | Aktif (SmartMotion enabled) |
| 4 | IPC | Aktif (SmartMotion enabled) |
| 5–8 | Channel5–8 | Slot kosong |

**SmartMotionDetect** aktif di channel 1–3 (minimum), dikonfigurasi untuk mendeteksi **Human** dengan sensitivitas **Middle**. Deteksi Vehicle dan Animal dimatikan.

### 12.3 ONVIF Services

NVR mendukung semua service ONVIF standar:

| Service | Endpoint |
|---|---|
| Device Management | `http://10.10.30.2/onvif/device_service` |
| Events | `http://10.10.30.2/onvif/event_service` |
| Media | `http://10.10.30.2/onvif/media_service` |
| PTZ | `http://10.10.30.2/onvif/ptz_service` |
| Imaging | `http://10.10.30.2/onvif/imaging_service` |
| Analytics | `http://10.10.30.2/onvif/analytics_service` |

### 12.4 Event System

#### Via ONVIF PullPoint (Rekomendasi)

NVR mendukung ONVIF `CreatePullPointSubscription` + `PullMessages`. Satu pull request (timeout 5–10s) mengembalikan batch notifikasi.

```python
# Contoh singkat (lihat dahua_explore.py untuk kode lengkap)
from onvif import ONVIFCamera
cam = ONVIFCamera("10.10.30.2", 80, USERNAME, PASSWORD, no_cache=True)
event_service = cam.create_events_service()
sub = event_service.CreatePullPointSubscription({'InitialTerminationTime': 'PT5M'})
pull_service = cam.create_pullpoint_service()
msg = pull_service.PullMessages({'MessageLimit': 100, 'Timeout': 'PT10S'})
```

**Struktur Notifikasi** (lxml element, namespace `http://www.onvif.org/ver10/schema`):

| Field | Contoh Nilai | Keterangan |
|---|---|---|
| `UtcTime` | `2026-05-23T12:23:09Z` | Waktu event di NVR |
| `PropertyOperation` | `Initialized` / `Changed` | Status properti |
| Source `VideoSourceConfigurationToken` | `00100` | Token channel ONVIF |
| Source `VideoAnalyticsConfigurationToken` | `00100` | Analytics config |
| Source `Rule` | `00100` | Rule yang triggered |
| Data `IsMotion` | `true` / `false` | Hasil deteksi motion |

**Catatan parsing:** zeep (onvif-zeep) tidak mendeserialize topic text dan Message body untuk format non-standar Dahua. Gunakan lxml langsung:

```python
tt_ns = "http://www.onvif.org/ver10/schema"
msg_el = notif.Message._value_1          # lxml._Element
ts     = msg_el.get('UtcTime')
src    = {si.get('Name'): si.get('Value')
          for si in msg_el.findall(f'.//{{{tt_ns}}}Source/{{{tt_ns}}}SimpleItem')}
data   = {si.get('Name'): si.get('Value')
          for si in msg_el.findall(f'.//{{{tt_ns}}}Data/{{{tt_ns}}}SimpleItem')}
```

#### Via CGI Event Stream (Long-Poll)

```
GET /cgi-bin/eventManager.cgi?action=attach&codes=[All]&heartbeat=5
```

Server menjawab dengan `Content-Type: multipart/x-mixed-replace; boundary=myboundary`. Setiap event dikirim sebagai bagian multipart:

```
--myboundary
Content-Type: text/plain

Code=VideoMotion; action=Start; index=0; data={...}
--myboundary
```

Endpoint ini menggunakan Digest Auth dan membutuhkan koneksi HTTP persisten (bukan standard `urllib`). Gunakan `http.client.HTTPConnection` dengan Digest Auth manual (lihat `dahua_explore.py` fungsi `listen_events()`).

**Event codes yang tersedia:**

| Code | Keterangan |
|---|---|
| `VideoMotion` | Deteksi gerakan standar |
| `SmartMotionHuman` | Deteksi manusia (AI) |
| `SmartMotionVehicle` | Deteksi kendaraan (AI) |
| `VideoLoss` | Kehilangan sinyal video |
| `VideoBlind` | Kamera tertutup/terblokir |
| `CrossLineDetection` | Penyeberangan garis virtual |
| `CrossRegionDetection` | Masuk/keluar area virtual |
| `AlarmLocal` | Alarm I/O lokal |
| `DiskFull` / `DiskError` | Status penyimpanan |
| `NetAbort` | Koneksi network terputus |

> Semua code tersedia di `/cgi-bin/eventManager.cgi?action=getEventIndexes&code=<Code>`. Jika tidak ada event aktif, NVR menjawab `Error: No Events` (bukan error — artinya channel tidak sedang aktif trigger).

### 12.5 Recording / Playback

NVR menyimpan rekaman di disk internal dengan format `.dav` (Dahua proprietary). Rekaman bisa ditemukan via `mediaFileFind.cgi` menggunakan stateful factory:

```bash
# 1. Buat factory object
GET /cgi-bin/mediaFileFind.cgi?action=factory.create
→ result=<object_id>

# 2. Mulai pencarian (channel=-1 untuk semua channel)
GET /cgi-bin/mediaFileFind.cgi?action=findFile&object=<id>&condition.Channel=-1&condition.StartTime=2026-05-22+00:00:00&condition.EndTime=2026-05-23+00:00:00

# 3. Ambil hasil
GET /cgi-bin/mediaFileFind.cgi?action=findNextFile&object=<id>&count=20

# 4. Tutup session
GET /cgi-bin/mediaFileFind.cgi?action=close&object=<id>
```

**Format entri rekaman:**

```
items[0].Channel=1
items[0].StartTime=2026-05-22 01:00:00
items[0].EndTime=2026-05-22 02:00:00
items[0].FilePath=/mnt/dvr/2026-05-22/1/dav/01/0/3/142833/01.00.00-02.00.00[R][0@0][0].dav
items[0].Length=951713792    # ~950MB per jam per channel
items[0].Type=dav
items[0].VideoStream=Main
items[0].Flags[0]=Timing     # Regular scheduled recording
items[0].Flags[1]=UnMarked
```

> **Note:** `condition.Channel` menggunakan nilai **0-indexed** untuk channel spesifik (ch1 = 0, ch2 = 1, dst.) atau **-1** untuk semua channel. Parameter `condition.Types[0]=dav` menyebabkan `400 Bad Request` pada model ini — hilangkan untuk hasil optimal.

### 12.6 Keterbatasan User `dashboard`

| Endpoint | Hasil | Keterangan |
|---|---|---|
| `configManager.cgi?name=VideoMotion` | **403 Forbidden** | Perlu akses admin |
| `snapshot.cgi` | **400 Bad Request** | Tidak diizinkan untuk user ini |
| `SystemInfo.cgi`, `diskManager.cgi`, `storagePoint.cgi` | **501 Not Implemented** | Tidak didukung model DHI-NVR4108HS |
| ONVIF `GetProfiles` / `GetSnapshotUri` | **Auth Error** | User `dashboard` tidak ada di ONVIF media service (hanya di CGI HTTP) |

### 12.7 Script Eksplorasi

**File:** `dahua_explore.py`

```bash
# Info dasar (device, channels, event types, network)
python3 dahua_explore.py --probe

# Listen event stream 60 detik (Ctrl+C untuk berhenti lebih awal)
python3 dahua_explore.py --events --event-duration 60

# Filter event codes tertentu
python3 dahua_explore.py --events --event-codes "VideoMotion,SmartMotionHuman"

# Daftar rekaman 24 jam terakhir (semua channel)
python3 dahua_explore.py --recordings

# Snapshot channel 1 ke /tmp/snap_ch1.jpg
python3 dahua_explore.py --snapshot 1

# Semua probe kecuali event stream
python3 dahua_explore.py --all

# Override host/credential
python3 dahua_explore.py --host 10.10.30.2 --user admin --password AdminPass --probe
```

Credentials juga bisa diset via environment variables: `DVR_HOST`, `DVR_USER`, `DVR_PASS`.

### 12.8 Integrasi Potensial ke Dashboard

| Fitur | Mekanisme | Estimasi |
|---|---|---|
| **Notifikasi motion real-time** | Backend poll ONVIF PullMessages tiap 5–10s, broadcast ke frontend via WebSocket | 4–8 jam |
| **Badge "On Motion" di CCTVPlayer** | WebSocket client di frontend, update per-channel state | 2–4 jam |
| **Timeline rekaman** | Backend `/api/recordings?channel=N&date=YYYY-MM-DD` → mediaFileFind | 4–6 jam |
| **Status NVR (waktu, uptime)** | Backend `/api/nvr-status` → `global.cgi?action=getCurrentTime` | 1 jam |
| **Health check HLS + NVR** | Gabungkan `/api/stream-status` dengan CGI ping | 2–3 jam |

---

## 13. Integrasi Kamera DH-P5AE-PV (Siren/Speaker)

> Ditambahkan: 31 Mei 2026

### 13.1 Kapabilitas Kamera

Dahua DH-P5AE-PV adalah kamera WiFi PT 5MP dengan fitur:

| Fitur | Spesifikasi |
|---|---|
| **Two-Way Audio** | Built-in mic + speaker |
| **Siren/Active Deterrence** | 1 preset + up to 10 custom sound alarm |
| **Smart Motion (SMD 3.0)** | Human & Vehicle detection |
| **IVS** | Tripwire, Intrusion, Linkage Tracking |
| **Pan/Tilt** | 0°–345° pan, 0°–80° tilt, 300 preset |
| **Konektivitas** | WiFi 802.11b/g/n (2.4 GHz) + RJ-45 |
| **ONVIF** | Device, Events, Media, PTZ |

### 13.2 Audio Output / Siren API

Integrasi menggunakan Dahua CGI HTTP API via Digest Auth:

```bash
# Trigger siren (coaxial control)
curl --digest -u user:pass \
  "http://<CAMERA-IP>/cgi-bin/coaxialControl.cgi?action=control&channel=1&info[0].Type=Speaker"

# Trigger alarm
curl --digest -u user:pass \
  "http://<CAMERA-IP>/cgi-bin/alarm.cgi?action=start&channel=1"

# Stop alarm
curl --digest -u user:pass \
  "http://<CAMERA-IP>/cgi-bin/alarm.cgi?action=stop&channel=1"
```

### 13.3 Integrasi di Dashboard

**Alur otomatis:**
```
Analyzer detect person (mode=away)
  → POST /api/analyzer-event
    → Backend _trigger_alarm()
      → HTTP CGI ke kamera DH-P5AE-PV
        → Speaker kamera bunyi siren
```

**Alur manual (API):**

| Method | Path | Body | Keterangan |
|---|---|---|---|
| POST | `/api/siren` | `{"channel": 1}` | Trigger siren |
| POST | `/api/siren/stop` | `{"channel": 1}` | Stop siren |

### 13.4 Konfigurasi

Environment variables di `docker-compose.yml` (service `backend`):

| Variable | Default | Keterangan |
|---|---|---|
| `SIREN_CAMERA_HOST` | (kosong) | IP kamera dengan speaker (DH-P5AE-PV) |
| `SIREN_CAMERA_USER` | fallback DVR_USER | Username kamera |
| `SIREN_CAMERA_PASS` | fallback DVR_PASS | Password kamera |
| `SIREN_ENABLED` | `true` | Enable/disable siren trigger |

---

## 14. NVR Guard Mode (Arm/Disarm)

> Ditambahkan: 31 Mei 2026

### 14.1 Mekanisme

NVR DHI-NVR4108HS-4KS3 mendukung guard mode via CGI API. Guard mode mengontrol apakah NVR akan memproses dan merekam alarm events.

### 14.2 API Endpoints

| Method | Path | Body | Keterangan |
|---|---|---|---|
| GET | `/api/nvr-guard` | — | Get current armed/disarmed status |
| POST | `/api/nvr-guard` | `{"armed": true}` | Set arm/disarm |

### 14.3 Sinkronisasi dengan Mode Home/Away

Ketika user mengganti mode via `POST /api/mode`:
- **mode=away** → NVR otomatis di-arm (background thread)
- **mode=home** → NVR otomatis di-disarm (background thread)

Sinkronisasi berjalan asinkron (non-blocking) agar tidak memperlambat UI response.

### 14.4 CGI Endpoints yang Digunakan

```bash
# Arm
GET /cgi-bin/configManager.cgi?action=setConfig&Alarm_ARM=Start

# Disarm
GET /cgi-bin/configManager.cgi?action=setConfig&Alarm_ARM=Stop

# Get status
GET /cgi-bin/configManager.cgi?action=getConfig&name=Alarm_ARM
```

**Catatan:** Membutuhkan user dengan hak **Config** dan **Alarm** di NVR. User `dashboard` (hak terbatas) kemungkinan mendapat 403. Gunakan `DVR_EVENT_USER` / `DVR_EVENT_PASS` dengan akun yang memiliki privilege lebih tinggi.

---

## 15. Optimasi MediaMTX — On-Demand Streaming

> Ditambahkan: 31 Mei 2026

### 15.1 Perubahan dari Always-On ke On-Demand

**Sebelumnya (v1):**
```yaml
paths:
  ch1:
    runOnInit: /app/start_stream.sh 1
    runOnInitRestart: yes
```
- ffmpeg berjalan terus 24/7 untuk semua channel
- CPU + bandwidth terpakai meskipun tidak ada viewer
- ~3000kbps × 4 channel = ~12 Mbps konstan dari NVR

**Sekarang (v2):**
```yaml
paths:
  ch1:
    runOnDemand: /app/start_stream.sh 1
    runOnDemandRestart: yes
    runOnDemandStartTimeout: 15s
    runOnDemandCloseAfter: 30s
```
- ffmpeg hanya start ketika ada viewer yang request stream
- Otomatis stop 30 detik setelah viewer terakhir disconnect
- Startup time ~5–10 detik (negoisasi RTSP + HLS segment pertama)

### 15.2 Dampak

| Aspek | Sebelum | Sesudah |
|---|---|---|
| CPU idle | ~20–30% (4× ffmpeg) | ~2% (mediamtx saja) |
| Bandwidth NVR | 12 Mbps konstan | 0 saat idle, 3 Mbps per viewer |
| Startup latency | 0 (sudah jalan) | 5–10 detik (first viewer) |
| Recovery | Auto-restart | Auto-restart + on-demand |

### 15.3 Catatan

- Analyzer service tetap bisa trigger stream on-demand karena RTSP read ke mediamtx dihitung sebagai "viewer"
- `runOnDemandCloseAfter: 30s` memberi buffer agar stream tidak stop/start terus jika user berpindah halaman sebentar
- Custom streams (`~^custom_[0-9a-f]+$`) tidak terpengaruh (tetap publisher-push)

---

## 16. Optimasi Analyzer — Mode-Aware On-Demand AI

> Ditambahkan: 31 Mei 2026

### 16.1 Arsitektur Baru

```
┌─────────────────────────────────────────────────────┐
│                 Analyzer Service                      │
│                                                      │
│  mode=home:                                          │
│    • Snapshot only (setiap 30 frame)                 │
│    • Tanpa AI inference                              │
│    • Model belum di-load (lazy init)                 │
│    • CPU usage: minimal                              │
│                                                      │
│  mode=away:                                          │
│    • Full AI processing (YOLO + InsightFace)         │
│    • Snapshot + inference setiap 8 frame             │
│    • Zone intrusion + face recognition aktif         │
│    • Alarm trigger ke siren kamera                   │
│                                                      │
│  Mode check: polling /api/mode setiap 30s            │
└─────────────────────────────────────────────────────┘
```

### 16.2 Perubahan Kunci

| Optimasi | Sebelum | Sesudah | Dampak |
|---|---|---|---|
| **Model loading** | Startup (blocking ~30s) | Lazy on first `mode=away` | Container start instant |
| **AI inference** | Selalu aktif | Hanya saat `mode=away` | ~90% CPU savings saat home |
| **Face detection** | 2× per person (zone + face) | 1× per person (cached) | ~50% face inference saved |
| **HTTP calls** | New connection per request | `requests.Session` pooling | ~100ms/request saved |
| **Snapshot saving** | Setiap 8 frame (selalu) | Setiap 30 frame (home), 8 frame (away) | Less disk I/O |
| **DB refresh** | Setiap 30s (selalu) | Setiap 30s hanya saat away | Less backend load |

### 16.3 Resource Usage (Estimasi 4 Channel)

| Mode | CPU | Memory | Network |
|---|---|---|---|
| **Home** | ~5% (RTSP read + snapshot) | ~100MB (no models) | Minimal |
| **Away** | ~30–50% (YOLO + face) | ~450MB (models loaded) | Moderate (events) |

### 16.4 Alur Mode Transition

```
User toggle → POST /api/mode {mode: "away"}
  → Backend saves mode + arms NVR (background)
  → Analyzer polls mode (next 30s cycle)
    → Detects mode=away
    → Loads models if not yet loaded (lazy)
    → Starts AI inference on next frame cycle
    → Detects person → triggers siren on camera
```

---

*Dokumen ini diperbarui pada 31 Mei 2026 dengan integrasi DH-P5AE-PV, NVR guard mode, on-demand streaming, dan optimasi analyzer.*  
*Perbarui dokumen ini setiap kali ada perubahan arsitektur signifikan.*
