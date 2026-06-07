# Performance Assessment

Tanggal ukur: 28 Mei 2026

## Ringkasan

Stack dashboard yang sedang aktif di mesin ini terdiri dari:
- `mtx`
- `backend`
- `frontend`

Service `analyzer` saat pengukuran **tidak sedang berjalan**, jadi beban AI realtime saat ini = **0% aktual**. Karena itu, angka di bawah adalah beban dashboard **tanpa analyzer**.

Pada Mac ini, bottleneck terbesar bukan frontend atau Flask, tetapi proses `ffmpeg` yang mentranskode stream di `mtx`, ditambah satu proses `ffmpeg` custom di `backend`.

## Mesin Uji

- CPU: Apple M1
- Logical CPU: 8
- Physical CPU: 8
- RAM host: 8 GiB

Catatan penting untuk macOS:
- Container Docker berjalan di dalam VM Docker Desktop.
- Karena itu, `docker stats` menunjukkan memori relatif ke limit VM Docker, bukan langsung ke seluruh RAM host.
- Di host, proses VM Docker (`com.apple.Virtualization.VirtualMachine`) juga terlihat sebagai beban tersendiri.

## Service Yang Aktif Saat Diukur

- `backend`
- `frontend`
- `mtx`

`analyzer` bersifat opsional dan ada di profile `ai`, jadi memang tidak ikut `docker compose up` biasa.

## Hasil Pengukuran Aktual

### Snapshot container 1

`docker stats --no-stream`

| Container | CPU | Memori | Catatan |
|---|---:|---:|---|
| `home-dashboard-mtx-1` | `420.37%` | `553.9 MiB` | beban terberat, dominan dari 3 stream built-in aktif + 1 stream lain ringan |
| `home-dashboard-backend-1` | `85.99%` | `257.6 MiB` | Flask + custom stream ffmpeg + pekerjaan event |
| `home-dashboard-frontend-1` | `3.13%` | `7.9 MiB` | sangat ringan |

Interpretasi terhadap total CPU host 8 core:
- `mtx`: sekitar `52.5%` kapasitas CPU total mesin
- `backend`: sekitar `10.7%`
- `frontend`: sekitar `0.4%`
- total stack: sekitar `63.7%` kapasitas CPU total mesin

### Snapshot container 2

`docker stats --no-stream`

| Container | CPU | Memori | Catatan |
|---|---:|---:|---|
| `home-dashboard-mtx-1` | `230.77%` | `536.5 MiB` | masih paling berat |
| `home-dashboard-backend-1` | `87.08%` | `258 MiB` | relatif stabil tinggi |
| `home-dashboard-frontend-1` | `0.66%` | `7.8 MiB` | tetap sangat ringan |

Interpretasi terhadap total CPU host 8 core:
- `mtx`: sekitar `28.8%` kapasitas CPU total mesin
- `backend`: sekitar `10.9%`
- `frontend`: sekitar `0.1%`
- total stack: sekitar `39.8%` kapasitas CPU total mesin

### Kesimpulan angka praktis

Tanpa analyzer aktif, dashboard ini saat diam namun sedang dipakai browser berada di kisaran:

- CPU total host: sekitar **40% sampai 64%** kapasitas mesin
- RAM container total: sekitar **800 MiB**, atau sekitar **10%** dari RAM host 8 GiB

Untuk frontend sendiri, bebannya nyaris tidak signifikan.

## Akar Beban Utama

### 1. `mtx` adalah komponen terberat

Proses di dalam container `mtx` yang paling berat adalah `ffmpeg` transcode built-in channels:

- channel 2: sekitar `59.8%` CPU
- channel 4: sekitar `56.9%` CPU
- channel 3: sekitar `53.0%` CPU
- channel 1: sekitar `2.6%` CPU saat source tidak benar-benar aktif

Semua proses ini berjalan dengan pipeline H.264/AAC transcode, sehingga `mtx` praktis menjadi mesin transcoding utama.

### 2. `backend` tidak cuma Flask

`backend` juga menanggung proses berikut:

- `gunicorn`
- `start_custom_stream.sh`
- `ffmpeg` untuk stream custom manual (`custom_9ec19eb589`)

Jadi CPU `backend` yang tinggi bukan semata-mata dari API Flask. Ada beban transcoding custom stream di sana.

### 3. Docker Desktop menambah overhead host

Di host macOS, proses VM Docker sempat terlihat sekitar:

- `205%` CPU
- `~745 MiB` RSS

Ini tidak boleh dijumlahkan mentah dengan `docker stats`, tetapi penting sebagai pengingat bahwa di Mac, container selalu punya overhead virtualisasi.

## Status AI Saat Ini

### Aktual sekarang

- `analyzer` **tidak berjalan**, jadi beban AI realtime = **0% aktual** pada sesi pengukuran ini.

### Jika analyzer diaktifkan

Service analyzer memuat:

- YOLOv8n
- InsightFace `buffalo_sc`
- OpenCV
- CPU execution provider

Dan memproses stream RTSP dari `mtx` dengan `PROCESS_EVERY=8`.

Artinya, ketika analyzer aktif, ia akan menjadi salah satu komponen terberat berikutnya setelah transcoding. Saya belum memasukkan angka live analyzer ke dokumen ini karena service itu tidak sedang aktif pada saat benchmark ini dibuat.

## Assessment Praktis

### Apakah setup ini ringan?

Untuk mesin Apple M1 8 GiB RAM:

- **frontend**: ringan
- **backend API**: ringan sampai sedang
- **custom stream di backend**: sedang
- **built-in transcoding di `mtx`**: berat
- **AI analyzer**: belum aktif saat benchmark, tetapi secara arsitektur akan menambah beban signifikan

Jadi jawaban singkatnya:

> Dashboard ini **tidak bisa disebut ringan** dalam mode sekarang, karena transcoding built-in stream terus-menerus sudah memakan porsi CPU yang besar bahkan tanpa analyzer aktif.

## Rekomendasi Prioritas Tertinggi

### 1. Ubah built-in stream menjadi on-demand

Saat ini channel built-in ditranskode terus di `mtx` melalui `runOnInit`.

Efeknya:
- CPU tetap tinggi walaupun tidak semua kamera sedang dilihat

Rekomendasi:
- ubah strategi supaya publisher built-in hanya aktif saat ada viewer
- gunakan pendekatan on-demand, bukan always-on

Ini kemungkinan adalah penghematan CPU terbesar.

### 2. Hindari transcode jika sumber sudah browser-friendly

Saat ini stream built-in ditranskode ke H.264/AAC.

Kalau NVR/camera bisa diset agar stream asal sudah:
- H.264
- AAC atau tanpa audio yang tidak dibutuhkan

maka target terbaik adalah:
- `-c:v copy`
- `-c:a copy` atau audio dimatikan

Menghilangkan transcode akan memangkas CPU secara drastis.

### 3. Turunkan bitrate stream dashboard

Saat ini ffmpeg built-in memakai sekitar:
- `-b:v 3000k`
- `-maxrate 3500k`
- `-bufsize 7000k`

Untuk dashboard monitor biasa, rekomendasi awal:
- coba `1500k` sampai `2000k`
- turunkan `maxrate` dan `bufsize` proporsional

Ini mengurangi beban encode dan bandwidth.

### 4. Pertimbangkan kembali `subtype=0` vs `subtype=1`

Saat ini main stream dipilih demi OSD/timestamp.

Tradeoff-nya:
- main stream = kualitas bagus, CPU tinggi
- substream = CPU lebih ringan, kualitas lebih rendah

Jika tujuan utama dashboard adalah monitoring ringan, substream tetap opsi paling murah secara compute.

### 5. Batasi custom stream manual

Custom stream di backend juga menjalankan ffmpeg sendiri.

Jika channel manual banyak, beban backend akan naik cepat.

Rekomendasi:
- pertahankan mode video-only bila audio tidak penting
- gunakan bitrate lebih rendah untuk channel manual
- nonaktifkan channel custom yang tidak perlu aktif terus

### 6. Playback event sebaiknya tetap pendek

Saat ini backend bisa membuat clip event lokal.

Itu bagus untuk UX, tetapi setiap clip berarti kerja ffmpeg tambahan.

Rekomendasi:
- jaga clip tetap pendek, mis. `8-12` detik
- bila perlu, ubah ke on-demand capture saat user klik playback, bukan otomatis saat semua event masuk

### 7. Saat AI diaktifkan, jaga analyzer tetap hemat

Untuk analyzer:

- pertahankan `PROCESS_EVERY` tinggi, mis. `8` atau lebih
- jangan aktifkan AI di semua channel bila tidak perlu
- batasi area deteksi dengan zone yang relevan
- pertimbangkan refresh interval yang lebih jarang bila data wajah/zona tidak sering berubah

Rekomendasi paling aman di mesin ini:
- nyalakan analyzer hanya saat benar-benar dibutuhkan

### 8. Jaga ekspektasi untuk mesin 8 GiB RAM

Dengan Docker Desktop + VS Code + browser + transcoding aktif:

- 8 GiB itu cukup untuk development ringan
- tetapi akan cepat terasa berat jika analyzer ikut aktif, banyak event playback, atau semua stream dipaksa transcode penuh

Kalau targetnya selalu aktif dan stabil, opsi terbaik:
- pindahkan workload stream/AI ke mesin terpisah
- atau jalankan dashboard tanpa analyzer di laptop ini

## Profil Operasional Yang Direkomendasikan

### Mode ringan untuk laptop development

- `frontend` aktif
- `backend` aktif
- `mtx` aktif hanya untuk channel yang benar-benar dilihat
- `analyzer` mati
- playback event tetap pendek

### Mode monitoring harian

- maksimal beberapa channel aktif bersamaan
- bitrate dashboard lebih rendah
- AI hanya di kamera penting

### Mode penuh

- semua stream transcode aktif
- analyzer aktif
- playback event aktif

Mode ini kemungkinan terlalu berat untuk pengalaman yang nyaman di Mac 8 GiB jika sambil bekerja di VS Code dan browser.

## Cara Re-check Nanti

Command yang paling berguna untuk audit cepat:

```bash
docker stats --no-stream
```

Untuk melihat service aktif:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml ps
```

Untuk melihat proses paling berat di `mtx`:

```bash
docker compose -f docker-compose.yml -f docker-compose.override.yml exec -T mtx sh -lc "ps -eo pid,pcpu,pmem,args --sort=-pcpu | sed -n '1,20p'"
```

## Final Verdict

Pada mesin ini, kondisi saat ini bisa diringkas sebagai berikut:

- beban dashboard **tanpa analyzer**: sudah **sedang sampai berat**
- penyebab utama: **transcoding ffmpeg di `mtx`**
- frontend: **bukan masalah**
- backend API: **bukan bottleneck utama**, tetapi custom stream ffmpeg di backend menambah beban
- analyzer: **belum aktif saat benchmark**, jadi belum dihitung live, tetapi bila dihidupkan kemungkinan besar akan membuat sistem terasa jauh lebih berat

Kalau target utama adalah tetap ringan, urutan optimasi paling efektif adalah:

1. built-in stream jadi on-demand
2. kurangi atau hilangkan transcode
3. pakai bitrate/substream yang lebih kecil
4. AI hanya aktif bila perlu
