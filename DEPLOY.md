# Deployment ke Server

Panduan deploy home-dashboard ke server Linux via Docker Compose. Semua service
(frontend, backend, mediamtx, analyzer AI) jalan otomatis — **tidak ada script
yang perlu dijalankan manual**. `start_custom_stream.sh` dipanggil sendiri oleh
backend saat startup / tambah kamera / restart stream; `start_stream.sh`
dipanggil sendiri oleh mediamtx saat ada viewer (on-demand).

## 1. Prasyarat

- Server Linux (x86_64 atau ARM64) dengan Docker Engine + plugin `docker compose`.
- Server bisa menjangkau NVR (mis. `10.10.30.2`) dan kamera IP (mis. `10.10.80.3`)
  di jaringannya.
- RAM disarankan ≥ 4 GB (analyzer YOLO + face recognition butuh ~1–1.5 GB).

## 2. Ambil kode & konfigurasi

```bash
git clone <https://github.com/dimitrijeremy/home-dashboard> home-dashboard
cd home-dashboard
cp .env.example .env
s
```

Isi `.env` minimal:

```env
DVR_HOST=10.10.30.2
DVR_USER=dashboard
DVR_PASS=<password>     # karakter $ WAJIB ditulis ganda: $$
NVR_CHANNELS=4
```

Catatan penting:
- **`$` dalam password harus ditulis `$$`** — docker compose menginterpolasi
  `$VAR` di file .env (kalau lupa, password terpotong diam-diam).
- Nilai .env hanya **seed awal saat database masih kosong**. Setelah jalan,
  semua host/kredensial dikelola dari menu Konfigurasi di dashboard dan
  tersimpan di database (volume `camera_data`).

## 3. Build & jalankan

```bash
docker compose up -d --build
```

Build pertama memakan waktu (image analyzer ~3 GB berisi PyTorch/YOLO).
Semua service punya `restart: unless-stopped` — otomatis hidup lagi setelah
server reboot.

Akses dashboard: `http://<ip-server>:8088`
(port bisa diganti di `docker-compose.yml` bagian `frontend.ports`).

## 4. Konfigurasi awal dari dashboard

1. **⚙ Konfigurasi → Kredensial NVR** — verifikasi host & kredensial
   (stream + event), lalu simpan.
2. **Kualitas Stream** — pilih **"Substream NVR (paling hemat)"** untuk server
   dengan CPU terbatas, lalu tekan **↺ Restart Semua** di panel status dashboard.
3. **Zona Perimeter / Wajah Dikenal** — gambar zona & daftarkan wajah untuk AI.
4. Kamera custom ditambah lewat tombol **＋ Tambah** (backend otomatis
   menjalankan publisher-nya, tanpa perlu SSH ke server).

### Format URL kamera custom

- Kamera/NVR Dahua umum:
  `rtsps://user:pass@HOST:554/cam/realmonitor?subtype=0&unicast=true&proto=Onvif&tls=true`
- **Kamera yang mewajibkan TLS tapi firmware-nya rewel (mis. DH-P5AE-PV / PTZ A)**:
  gunakan URL polos **tanpa** `unicast`/`proto`:
  `rtsps://user:pass@HOST:554/cam/realmonitor?subtype=0`
  Backend otomatis membuka jembatan TLS (socat) untuk semua URL `rtsps://`.
- Nomor channel diisi terpisah di form (jangan taruh `channel=` di URL).

## 5. Update / redeploy

```bash
cd home-dashboard
git pull
docker compose build
docker compose up -d
```

Data (database kamera/zona/wajah, snapshot, klip event) aman di volume
`camera_data` — tidak hilang saat rebuild. Jika hanya mengedit
`mtx/start_stream.sh` atau `mtx/mediamtx.yml` (di-bind-mount), cukup:

```bash
docker compose up -d --force-recreate mtx
```

Jika mengedit `backend/` (termasuk `start_custom_stream.sh` — dibake ke image):

```bash
docker compose build backend && docker compose up -d backend
```

## 6. Panel resource server

Panel "Server" di dashboard membaca Docker socket yang di-mount read-only ke
backend (`/var/run/docker.sock`). Ini sudah diatur di `docker-compose.yml` dan
bekerja langsung di server Linux. Kalau tidak ingin mount socket, hapus baris
mount-nya — panel tetap menampilkan CPU/RAM/disk host, hanya daftar container
yang hilang.

## 7. Menghemat resource (server kecil)

- Kualitas stream **Substream** (lihat langkah 4.2) — pengaruh terbesar.
- Di `.env`, analyzer bisa diringankan lalu `docker compose up -d analyzer`:
  ```env
  PROCESS_EVERY=12      # proses 1 dari 12 frame
  YOLO_IMGSZ=480        # inferensi lebih kecil
  FACE_EVERY_SECS=3.0
  ```
- Matikan AI per kamera dari modal edit kamera (toggle AI) — worker analyzer
  untuk kamera itu berhenti otomatis ≤ 60 detik.
- `docker-compose.yml` sudah membatasi CPU/RAM per service (`cpus`/`mem_limit`)
  supaya satu container yang spike tidak ikut membekukan seluruh host. Kalau
  server-nya lebih kecil/besar dari NUC 4-core, sesuaikan nilainya.

## 7b. Hardware encode (Intel Quick Sync / VAAPI) — opsional

Semua transcode default pakai `libx264` software encode — paling berat di
CPU. Kalau server punya iGPU Intel (NUC/mini PC modern), encode bisa
dialihkan ke GPU (jauh lebih ringan di CPU):

1. Cek dulu device-nya ada: `ls /dev/dri` di server — harus muncul `card0`
   dan `renderD128`. Kalau tidak ada (mis. jalan di VM tanpa GPU passthrough),
   **jangan lanjut** — langkah 2 akan bikin service gagal start.
2. Di `docker-compose.yml`, uncomment 2 baris `devices: - /dev/dri:/dev/dri`
   di service `mtx` dan `backend`.
3. Tambahkan di `.env`:
   ```env
   HWACCEL=vaapi
   ```
4. `docker compose up -d --build`, lalu cek `docker compose logs mtx` dan
   `docker compose logs backend` — kalau ada error terkait `vaapi`/
   `renderD128`, biasanya driver iGPU tidak cocok/tidak aktif.
5. Rollback instan tanpa revert compose: kosongkan `HWACCEL=` di `.env` lalu
   `docker compose up -d` — otomatis balik ke software encode.

## 8. Troubleshooting cepat

| Gejala | Cek |
|---|---|
| Channel built-in hitam | `docker logs home-dashboard-mtx-1` — error ffmpeg persis ada di sini |
| Kamera custom hitam | `docker exec home-dashboard-backend-1 cat /tmp/custom_<id>.log` |
| Kredensial NVR | Dikelola di DB (menu Konfigurasi), **bukan** .env setelah seed pertama |
| Akun Dahua terkunci (403 RmLock) | Tunggu sesuai detik RmLock; jangan spam restart |
| Delay membesar | Cek CPU di panel Server — kalau jenuh, turunkan kualitas stream |
| ch1 selalu offline | Normal — channel 1 NVR memang tidak ada kameranya |
| Error `vaapi`/`renderD128` setelah aktifkan HWACCEL | Kosongkan `HWACCEL=` di `.env`, `docker compose up -d` — rollback ke software encode |
