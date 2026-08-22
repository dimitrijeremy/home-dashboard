# Monitoring & Keamanan Jaringan

Dokumen ini lahir dari insiden nyata: muncul akun admin misterius (memo
"CISA") di NVR — indikasi kuat NVR pernah diakses pihak yang tidak dikenal.

## 0. Langkah darurat NVR (lakukan SEKARANG, sebelum yang lain)

Perlakukan NVR sebagai perangkat yang sudah pernah dibobol:

1. **Hapus akun asing itu** dan **ganti password SEMUA akun** NVR (terutama
   admin). Password panjang & unik, bukan variasi password lama.
2. **Update firmware NVR** ke versi terbaru dari situs resmi Dahua — akun
   misterius biasanya masuk lewat exploit firmware lama.
3. **Matikan akses NVR ke/dari internet** — ini yang paling penting:
   - Cek router: hapus semua **port forwarding** ke IP NVR (80/443/554/37777).
   - Matikan **UPnP** di router (NVR bisa buka port sendiri lewat UPnP).
   - Matikan fitur **P2P/cloud (DMSS/Easy4IP)** di NVR kalau tidak dipakai —
     ini kanal keluar yang aktif terus walau tidak ada port forward.
   - Idealnya: blok IP NVR & kamera dari WAN sepenuhnya di firewall routeXr.
     NVR tidak butuh internet untuk fungsi CCTV lokal.
4. Cek pengaturan **DDNS** dan **email** di NVR — penyerang kadang menaruh
   konfigurasi untuk akses ulang.
5. Setelah bersih, dashboard ini sekarang **memantau otomatis** (lihat § 1c).

## 1. Monitoring akses jaringan — review opsi

### Fakta dasar dulu

Server dashboard (NUC) **tidak bisa melihat trafik yang lewat router** dengan
sendirinya — di jaringan ter-switch, paket antar perangkat lain tidak pernah
mampir ke NUC. Supaya bisa "lihat trafik masuk dari mana saja dan ke mana",
salah satu dari ini harus tersedia dari sisi router/switch:

### Opsi A — DNS logging (paling praktis, rekomendasi mulai dari sini)

Jalankan **AdGuard Home** (container) di NUC sebagai DNS server jaringan, lalu
arahkan DHCP router supaya semua perangkat pakai DNS itu.

- Dapat: log **semua query domain per perangkat** — langsung kelihatan kalau
  NVR/perangkat lain menghubungi domain yang "tidak ada peruntukannya",
  plus bisa langsung **blokir** domain/kategori.
- Tidak dapat: koneksi langsung ke IP (tanpa DNS) tidak terlihat.
- Syarat: cuma akses admin router untuk ganti DNS di DHCP. Tidak butuh
  hardware baru.

### Opsi B — NetFlow/sFlow export dari router (paling lengkap)

Kalau router terluar mendukung NetFlow/IPFIX/sFlow (MikroTik: "Traffic Flow",
pfSense/OPNsense: softflowd, Ubiquiti: DPI/flows), router bisa mengekspor
ringkasan **semua koneksi** (src/dst IP, port, byte) ke collector di NUC —
data masuk ke Postgres monitoring yang sama, dianalisa polanya dari dashboard
atau DBeaver.

- Dapat: gambaran penuh koneksi masuk/keluar, termasuk yang tanpa DNS.
- Syarat: **tergantung merk/model router** — kabari saya router terluarnya
  apa (merk + model), baru collector-nya saya buatkan.

### Opsi C — Port mirroring + ntopng/Zeek (paling detail, paling ribet)

Butuh switch managed yang bisa mirror port uplink ke port NUC. Paling dalam
(bisa lihat isi/ukuran tiap koneksi real-time) tapi butuh hardware yang
mendukung dan CPU ekstra. Baru layak kalau A + B terbukti kurang.

### 1c. Yang SUDAH jalan sekarang di dashboard (tanpa syarat apa pun)

- **NVR Security Watch** — tiap 5 menit backend:
  - Mengambil daftar akun user NVR dan membandingkan dengan snapshot
    sebelumnya → akun baru/hilang memunculkan event `NvrUserAdded` /
    `NvrUserRemoved` di feed event dashboard. Kejadian "akun CISA" berikutnya
    ketahuan maksimal 5 menit, bukan kebetulan.
  - Membaca log login NVR → login oleh user selain akun service dashboard
    memunculkan event `NvrLoginDetected` (username + IP asal).
- **Log login dashboard** — semua percobaan login (berhasil/gagal/diblokir
  whitelist) tercatat dengan IP asal, tampil di tab Users & tersinkron ke
  Postgres.

## 2. Database monitoring (akses DBeaver dari luar)

Container `pg` (Postgres 16) berisi **salinan** tabel monitoring, disinkron
satu arah dari database utama tiap 60 detik: `login_log`,
`detection_events`, `nvr_events`, `cameras` (kredensial di URL di-redact;
tabel `users`/`settings` sengaja TIDAK disalin karena berisi password).

Koneksi DBeaver:

| Field    | Nilai |
|---|---|
| Host     | IP server (mis. `10.10.40.10`) |
| Port     | `5432` |
| Database | `monitoring` |
| Username | `monitor` (env `PG_USER`) |
| Password | env `PG_PASS` di `.env` — **GANTI default-nya sebelum deploy** |

Karena port 5432 terbuka ke LAN, wajib: password kuat di `PG_PASS`, dan kalau
router mendukung, batasi akses port 5432 hanya dari segmen manajemen.

## 3. Whitelist segmen login

Login dashboard hanya diizinkan dari CIDR yang di-whitelist
(default: `10.10.100.0/24, 10.10.80.0/24, 127.0.0.1/32` — dari env `ALLOWED_LOGIN_CIDRS`,
hanya seed awal; setelah itu dikelola dari tab **Users & Login**).
Percobaan dari luar whitelist ditolak (403) dan tercatat 🚫 di riwayat.

Anti-lockout, dua lapis:

1. UI menolak menyimpan whitelist yang tidak memuat IP kamu sendiri.
2. **Pintu darurat**: kalau tetap terkunci (mis. pindah segmen), tambahkan di
   `.env` server: `EXTRA_LOGIN_CIDRS=10.10.40.0/24` lalu
   `docker compose up -d backend` — env ini selalu di-union dengan whitelist.

> ⚠ Sebelum deploy pertama: kalau PC kamu BUKAN di 10.10.100.x / 10.10.80.x,
> isi dulu `EXTRA_LOGIN_CIDRS` di `.env` dengan segmen PC kamu, atau ubah
> `ALLOWED_LOGIN_CIDRS`. Kalau tidak, kamu tidak akan bisa login sama sekali.

## 4. HTTPS (akses secure, praktis)

Model: **CA lokal sekali install** — bukan cert publik, tidak ada biaya, tidak
tergantung internet.

```bash
cd /opt/hdash/home-dashboard
./tls/gen-certs.sh 10.10.40.10          # tambah IP/host lain kalau perlu
docker compose restart frontend
```

Lalu akses `https://10.10.40.10:8443`. Supaya browser tidak warning, install
`tls/certs/ca.crt` **sekali per perangkat** (inilah "install profile"):

- **iPhone/iPad**: kirim `ca.crt` (AirDrop/email) → Settings akan menawarkan
  "Profile Downloaded" → Install. Lalu **wajib**: Settings → General → About →
  Certificate Trust Settings → aktifkan trust untuk "Home Dashboard CA".
- **macOS**: double-click `ca.crt` → Keychain Access → cari "Home Dashboard
  CA" → Get Info → Trust → "Always Trust".
- **Windows**: double-click → Install Certificate → Local Machine → letakkan
  di "Trusted Root Certification Authorities".
- **Android**: Settings → Security → Install a certificate → CA certificate.

Soal "ganti cert tiap beberapa bulan": **tidak perlu**. CA berlaku 10 tahun
dan itu satu-satunya yang di-install di perangkat. Sertifikat server-nya
berlaku ~2 tahun 2 bulan (800 hari — batas maksimum yang diterima
iPhone/Mac untuk CA non-publik adalah 825 hari, jadi "selamanya" memang
tidak mungkin di perangkat Apple). Perpanjangan = jalankan ulang
`./tls/gen-certs.sh` + restart frontend; **perangkat tidak perlu di-apa-apakan**.

Setelah semua perangkat pakai HTTPS, aktifkan `COOKIE_SECURE=true` di `.env`
supaya cookie sesi tidak pernah terkirim lewat HTTP polos. (Catatan: setelah
ini login lewat `http://...:8088` tidak akan bisa — pakai selalu `:8443`.)

Alternatif kalau punya domain publik + akses API DNS (Cloudflare dll):
Let's Encrypt DNS-01 via Caddy — nol install profile di perangkat dan
perpanjangan full otomatis. Kabari kalau mau jalur ini.

---

## 5. Housekeeping storage (volume `camera_data`)

### Kenapa bisa membengkak

Volume `camera_data` menampung tiga hal dengan sifat berbeda:

| Isi | Lokasi | Sifat |
|---|---|---|
| `cameras.db` | `/data/cameras.db` | tumbuh pelan (teks) |
| Foto wajah terdaftar | `/data/face_photos/` | kecil, dikelola manual dari UI |
| Snapshot live per channel | `/data/snapshots/chN.jpg` | ditimpa terus, ukuran tetap |
| **Media event** | `/data/snapshots/nvr_events/`, `nvr_event_clips/`, `face_events/` | **tumbuh tanpa batas** |

Biang keroknya baris terakhir. Setiap event NVR ber-`action=Start` dengan code
di `NVR_EVENT_PREVIEW_CODES` memicu dua tulisan sekaligus:

1. `_capture_nvr_event_snapshot()` — 1 JPEG resolusi penuh dari NVR;
2. `_capture_nvr_event_clip()` — 1 MP4 sepanjang `NVR_EVENT_CLIP_SECS` (12 detik)
   yang diambil `-c copy` dari main stream, jadi **bitrate penuh**. Pada 4 Mbps
   itu ±6 MB per clip.

`VideoMotion` ikut di daftar itu, dan NVR mengirimnya berulang selama masih ada
gerakan — satu orang lewat depan kamera gampang jadi belasan event, belasan
clip, isinya nyaris sama.

### Rem yang dipasang

Dua-duanya di `backend/app.py`, semua lewat env di `docker-compose.yml`:

**Rem produksi** — `NVR_EVENT_NOISY_COOLDOWN_SECS` (default 60).
`_claim_capture_slot()` membatasi `VideoMotion`/`SmartMotionHuman` jadi maksimal
1 media per channel per 60 detik. Event-nya **tetap tercatat lengkap** di log dan
di DB; yang dilewat hanya snapshot + clip-nya. Event AI dan wajah
(`ZoneIntrusion`, `UnknownFace`, `FaceRecognized`, `CrossLine/CrossRegion`)
sengaja **tidak** kena cooldown — itu bukti kejadian.

**Rem penghapusan** — thread `media-janitor`, jalan tiap `MEDIA_JANITOR_SECS`
(default 600 detik). Urutan kerjanya:

1. pangkas baris `nvr_events`/`detection_events` di atas `EVENT_DB_MAX_ROWS`
   berikut filenya;
2. hapus file event lebih tua dari `MEDIA_RETENTION_DAYS`;
3. hapus file **tertua duluan** sampai total ≤ `MEDIA_MAX_GB`;
4. NULL-kan kolom `snapshot_path`/`clip_path` yang filenya sudah hilang, supaya
   UI tidak menampilkan thumbnail dan tombol playback yang pasti 404.

Janitor **hanya** menyentuh tiga direktori media event. `face_photos/`,
`snapshots/chN.jpg`, dan `cameras.db` tidak pernah ikut terhapus.

### Setelan

```bash
MEDIA_MAX_GB=40                    # 0 = tanpa batas (perilaku lama)
MEDIA_RETENTION_DAYS=30            # 0 = tanpa batas umur
MEDIA_JANITOR_SECS=600
EVENT_DB_MAX_ROWS=50000
NVR_EVENT_NOISY_COOLDOWN_SECS=60   # 0 = capture semua (perilaku lama)
NVR_EVENT_CLIP_SECS=12             # pengali langsung ukuran per clip
```

> **PERINGATAN:** menurunkan `MEDIA_MAX_GB` langsung menghapus file lama secara
> permanen pada siklus janitor berikutnya. Backup dulu kalau ada clip yang masih
> dibutuhkan.

### Cek kondisi

```bash
# Rincian per direktori + status janitor terakhir (butuh sesi login)
curl -sk https://<server-ip>:8443/api/storage | jq

# Dari sisi host
docker compose exec backend du -sh /data/* /data/snapshots/*
docker compose logs backend | grep JANITOR
```

Kalau volume masih membesar setelah janitor jalan, lever berikutnya berurutan
dari yang paling besar dampaknya: keluarkan `VideoMotion` dari
`NVR_EVENT_PREVIEW_CODES`, turunkan `NVR_EVENT_CLIP_SECS`, atau ambil clip dari
substream (`stream_quality=sub`) supaya bitrate-nya jauh lebih kecil.
