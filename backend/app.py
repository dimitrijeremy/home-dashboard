import atexit
import base64
import collections
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit
from uuid import uuid4
import requests
from requests.auth import HTTPDigestAuth
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask, Response, jsonify, request, g, send_file, session, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = os.getenv("BASE_URL", "http://localhost:8888")
# INTERNAL_HLS_URL: URL yang digunakan backend container untuk cek stream status.
# Jika BASE_URL kosong (server: stream URL relatif, proxy via nginx), set ini ke
# URL internal mediamtx container, mis. http://mediamtx:8888
INTERNAL_HLS_URL = os.getenv("INTERNAL_HLS_URL", BASE_URL).rstrip('/')
MTX_API_URL = (os.getenv("MTX_API_URL") or "http://mtx:9997").rstrip('/')
DB_PATH  = os.getenv("DB_PATH", "/data/cameras.db")
CUSTOM_STREAM_SCRIPT = os.getenv("CUSTOM_STREAM_SCRIPT") or os.path.join(APP_DIR, "start_custom_stream.sh")
FACE_PHOTO_DIR = os.getenv("FACE_PHOTO_DIR", "/data/face_photos")
SNAPSHOT_DIR   = os.getenv("SNAPSHOT_DIR",   "/data/snapshots")
NVR_EVENT_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "nvr_events")
NVR_EVENT_CLIP_DIR = os.path.join(SNAPSHOT_DIR, "nvr_event_clips")
FACE_EVENT_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "face_events")
MTX_RTSP_HOST = os.getenv("MTX_HOST", "mtx")
MTX_RTSP_PORT = int(os.getenv("RTSP_PORT", "8554"))
NVR_EVENT_CLIP_SECS = int(os.getenv("NVR_EVENT_CLIP_SECS", "12"))
CUSTOM_STREAM_PROCS = {}
NVR_EVENT_PREVIEW_CODES = {
    "SmartMotionHuman",
    "VideoMotion",
    "CrossRegionDetection",
    "CrossLineDetection",
    "ZoneIntrusion",
    "UnknownFace",
    "FaceRecognized",
}
NVR_EVENT_FETCH_LIMIT = 80

# ── Housekeeping media event ──────────────────────────────────────────────
# Tiap event "Start" dari NVR menulis 1 JPEG snapshot + 1 clip MP4 selama
# NVR_EVENT_CLIP_SECS detik dengan `-c copy` (bitrate penuh main stream).
# Tanpa rem, volume camera_data tumbuh tanpa batas. Dua rem dipasang:
#   1. cooldown per (channel, code) untuk kode motion yang datang beruntun,
#      supaya satu orang lewat tidak jadi puluhan clip yang isinya sama;
#   2. budget disk keras — file tertua dihapus sampai total <= MEDIA_MAX_GB.
# Janitor HANYA menyentuh direktori media event di _MEDIA_DIRS. Foto wajah
# terdaftar (FACE_PHOTO_DIR), snapshot live per-channel untuk zone editor, dan
# cameras.db tidak pernah ikut terhapus.
MEDIA_MAX_GB = float(os.getenv("MEDIA_MAX_GB", "40") or 0)          # 0 = tanpa batas
MEDIA_RETENTION_DAYS = int(os.getenv("MEDIA_RETENTION_DAYS", "30") or 0)  # 0 = tanpa batas umur
MEDIA_JANITOR_SECS = max(int(os.getenv("MEDIA_JANITOR_SECS", "600") or 600), 60)
# Baris event lama ikut dipangkas supaya cameras.db tidak membengkak sendiri.
EVENT_DB_MAX_ROWS = int(os.getenv("EVENT_DB_MAX_ROWS", "50000") or 0)
# Kode motion generik — dibatasi 1 capture per channel per N detik. Event AI /
# wajah (ZoneIntrusion, UnknownFace, FaceRecognized, CrossLine/CrossRegion)
# sengaja TIDAK masuk sini: itu bukti kejadian, jangan sampai ada yang hilang.
NVR_EVENT_NOISY_CODES = {"VideoMotion", "SmartMotionHuman"}
NVR_EVENT_NOISY_COOLDOWN_SECS = int(os.getenv("NVR_EVENT_NOISY_COOLDOWN_SECS", "60") or 0)
_MEDIA_DIRS = (NVR_EVENT_CLIP_DIR, NVR_EVENT_SNAPSHOT_DIR, FACE_EVENT_SNAPSHOT_DIR)
_capture_cooldown = {}
_capture_cooldown_lock = threading.Lock()
_media_usage = {"bytes": 0, "files": 0, "oldest": None, "last_run": None}

# Pilihan kualitas stream (setting global, key 'stream_quality'):
#   source — main stream apa adanya (subtype 0, tanpa scale)
#   720 / 480 — main stream di-scale turun saat transcode (hemat bandwidth/encode)
#   sub — pakai substream NVR/kamera (subtype 1) — paling hemat resource
STREAM_QUALITIES = {"source", "720", "480", "sub"}


def _public_stream_url(path_name):
    return f"{BASE_URL}/{path_name}/index.m3u8" if BASE_URL else f"/{path_name}/index.m3u8"


def _rewrite_stream_url_for_current_runtime(stream_url):
    stream_url = (stream_url or "").strip()
    if not stream_url:
        return stream_url

    parts = urlsplit(stream_url)
    if parts.scheme in {"http", "https"} and parts.hostname in {"localhost", "127.0.0.1"}:
        path = parts.path.strip("/")
        if path:
            return _public_stream_url(path.split("/")[0])

    return stream_url


def _mtx_api_request(method, path):
    if not MTX_API_URL:
        return None

    try:
        res = requests.request(method, f"{MTX_API_URL}{path}", timeout=3)
        res.raise_for_status()
        if not res.text:
            return {}
        return res.json()
    except Exception:
        return None


def _mtx_paths_map():
    payload = _mtx_api_request("GET", "/v3/paths/list") or {}
    items = payload.get("items") or []
    return {item.get("name"): item for item in items if item.get("name")}


def _mtx_kick_publisher(path_seg):
    path_info = _mtx_api_request("GET", f"/v3/paths/get/{path_seg}") or {}
    source = path_info.get("source") or {}
    if source.get("type") != "rtspSession" or not source.get("id"):
        return False
    return _mtx_api_request("POST", f"/v3/rtspsessions/kick/{source['id']}") is not None

# ── NVR Config ──────────────────────────────────────────────
# Nilai env hanya dipakai sebagai seed awal; nilai aktif disimpan di tabel
# settings (editable dari menu konfigurasi dashboard). Tidak ada default
# kredensial/host di kode.
DVR_HOST      = os.getenv("DVR_HOST", "")
DVR_HTTP_PORT = int(os.getenv("DVR_HTTP_PORT", "80"))
DVR_USER      = os.getenv("DVR_USER", "")
DVR_PASS      = os.getenv("DVR_PASS", "")
# Separate credentials for the event stream (needs operator/admin on Dahua).
# Falls back to DVR_USER/DVR_PASS if not configured.
DVR_EVENT_USER = os.getenv("DVR_EVENT_USER") or DVR_USER
DVR_EVENT_PASS = os.getenv("DVR_EVENT_PASS") or DVR_PASS

# ── Auth ──────────────────────────────────────────────────────
# ADMIN_USER/ADMIN_PASS: seed satu kali untuk user pertama saat tabel users
# masih kosong (mis. baru deploy) — sesudahnya user dikelola dari menu
# Konfigurasi > Users, bukan dari env lagi. GANTI password default ini
# segera setelah login pertama.
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "admin123")
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "10"))
# Whitelist segmen sumber yang boleh login (CIDR, dipisah koma). Nilai env
# hanya seed awal setting 'login_whitelist'; sesudahnya dikelola dari UI.
ALLOWED_LOGIN_CIDRS = os.getenv("ALLOWED_LOGIN_CIDRS", "10.10.100.0/24,10.10.80.0/24,127.0.0.1/32")
# Pintu darurat anti-lockout: CIDR ekstra dari env yang SELALU di-union dengan
# whitelist di DB — kalau terkunci (PC admin tidak masuk whitelist), tambahkan
# segmen di sini lalu `docker compose up -d backend`, tanpa perlu bedah DB.
EXTRA_LOGIN_CIDRS = os.getenv("EXTRA_LOGIN_CIDRS", "")


def _get_or_create_secret_file(path, nbytes=32):
    """Baca token persisten dari file di /data (shared volume); generate acak
    kalau belum ada. Dipakai untuk Flask session secret key dan token internal
    antar-container — sengaja file-based (bukan DB) supaya tersedia sebelum
    tabel settings ada, dan bisa dibaca container lain yang mount volume sama."""
    try:
        with open(path, 'r') as f:
            existing = f.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = secrets.token_hex(nbytes)
    with open(path, 'w') as f:
        f.write(token)
    os.chmod(path, 0o600)
    return token


_DATA_DIR = os.path.dirname(DB_PATH)
os.makedirs(_DATA_DIR, exist_ok=True)
app.secret_key = _get_or_create_secret_file(os.path.join(_DATA_DIR, '.flask_secret'))
# Dipakai mtx/analyzer (server-to-server, tanpa sesi browser) supaya tetap bisa
# mengakses /api/* internal tanpa lolos lewat halaman login.
INTERNAL_API_TOKEN = _get_or_create_secret_file(os.path.join(_DATA_DIR, '.internal_token'))

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    # True hanya kalau di depan reverse proxy HTTPS — default LAN pakai HTTP polos.
    SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', '').strip().lower() == 'true',
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=SESSION_TIMEOUT_MINUTES),
    SESSION_REFRESH_EACH_REQUEST=True,
)

# Endpoint yang boleh diakses tanpa sesi login (nama fungsi view, bukan path).
_PUBLIC_API_ENDPOINTS = {'login_route', 'session_status'}


@app.before_request
def _require_auth():
    if not request.path.startswith('/api/'):
        return None
    if request.endpoint in _PUBLIC_API_ENDPOINTS:
        return None
    # Server-to-server (mtx/analyzer) — bukan browser, tidak punya sesi.
    if secrets.compare_digest(request.headers.get('X-Internal-Token', ''), INTERNAL_API_TOKEN):
        return None
    if session.get('user_id'):
        session.permanent = True
        return None
    return jsonify({'error': 'unauthorized'}), 401

_nvr_events = collections.deque(maxlen=30)
_nvr_status = {"connected": False, "error": None, "last_event": None}
_nvr_lock   = threading.Lock()
_nvr_cond   = threading.Condition(_nvr_lock)
_nvr_revision = 0
_camera_write_lock = threading.Lock()


def _nvr_bump_locked():
    global _nvr_revision
    _nvr_revision += 1
    _nvr_cond.notify_all()


def _nvr_snapshot_locked():
    return {
        "status": dict(_nvr_status),
        "events": list(_nvr_events),
        "revision": _nvr_revision,
    }


def _db_connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _parse_channel_number(channel_value=None, index=None):
    if channel_value is not None:
        text = str(channel_value).strip()
        match = re.search(r"(\d+)$", text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
    if index is None:
        return None
    try:
        return int(index) + 1
    except (TypeError, ValueError):
        return None


def _should_capture_event_snapshot(code, action):
    return action == "Start" and code in NVR_EVENT_PREVIEW_CODES


def _capture_nvr_event_snapshot(channel_number, code):
    nvr_host = _get_nvr_host()
    if not channel_number or not nvr_host:
        return None

    os.makedirs(NVR_EVENT_SNAPSHOT_DIR, exist_ok=True)
    safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", code or "event")[:40]
    snapshot_name = (
        f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_"
        f"ch{channel_number}_{safe_code}_{uuid4().hex[:8]}.jpg"
    )
    snapshot_path = os.path.join(NVR_EVENT_SNAPSHOT_DIR, snapshot_name)
    snapshot_url = f"http://{nvr_host}:{_get_nvr_http_port()}/cgi-bin/snapshot.cgi?action=get&channel={channel_number}"

    last_error = "snapshot unavailable"
    for user, passwd in _nvr_auth_candidates():
        try:
            resp = requests.get(snapshot_url, auth=HTTPDigestAuth(user, passwd), timeout=(5, 10))
        except Exception as exc:
            last_error = str(exc)
            continue

        if resp.status_code == 200 and (resp.headers.get("Content-Type") or "").startswith("image/"):
            with open(snapshot_path, "wb") as fh:
                fh.write(resp.content)
            return snapshot_path

        last_error = f"HTTP {resp.status_code}"

    print(f"[NVR] snapshot capture failed for ch{channel_number}: {last_error}", flush=True)
    return None


def _save_face_event_photo(photo_b64, event_type):
    """Simpan crop wajah yang dikirim analyzer (frame asli saat deteksi) —
    lebih akurat & lebih cepat daripada _capture_nvr_event_snapshot yang
    minta ulang snapshot channel penuh ke NVR setelah kejadiannya lewat."""
    if not photo_b64:
        return None
    try:
        raw = base64.b64decode(photo_b64)
    except Exception:
        return None
    if not raw:
        return None
    os.makedirs(FACE_EVENT_SNAPSHOT_DIR, exist_ok=True)
    safe_code = re.sub(r"[^A-Za-z0-9_-]+", "_", event_type or "face")[:40]
    snapshot_name = (
        f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{safe_code}_{uuid4().hex[:8]}.jpg"
    )
    snapshot_path = os.path.join(FACE_EVENT_SNAPSHOT_DIR, snapshot_name)
    with open(snapshot_path, "wb") as fh:
        fh.write(raw)
    return snapshot_path


def _serialize_nvr_event_row(row):
    event = {
        "id": row["id"],
        "ts": row["ts"],
        "code": row["code"],
        "action": row["action"],
        "index": row["event_index"],
        "channel_number": row["channel_number"],
        "source": row["source"],
    }
    if row["snapshot_path"]:
        event["snapshot_url"] = f"/api/nvr-events/{row['id']}/snapshot"
    if row["clip_path"]:
        event["clip_url"] = f"/api/nvr-events/{row['id']}/clip"
    if row["extra_json"]:
        try:
            extra = json.loads(row["extra_json"])
        except Exception:
            extra = None
        if isinstance(extra, dict):
            event.update(extra)
    return event


def _fetch_recent_nvr_events(limit=NVR_EVENT_FETCH_LIMIT):
    con = _db_connect()
    try:
        rows = con.execute(
            """SELECT id, ts, code, action, event_index, channel_number, source, snapshot_path, clip_path, extra_json
               FROM nvr_events ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    finally:
        con.close()
    return [_serialize_nvr_event_row(row) for row in rows]


def _nvr_payload_snapshot(limit=NVR_EVENT_FETCH_LIMIT):
    with _nvr_lock:
        status = dict(_nvr_status)
        revision = _nvr_revision
    return {
        "status": status,
        "events": _fetch_recent_nvr_events(limit=limit),
        "revision": revision,
    }


def _store_nvr_event(event, snapshot_path=None):
    extra = {
        key: value
        for key, value in event.items()
        if key not in {"id", "ts", "code", "action", "index", "channel_number", "source", "snapshot_url"}
    }
    con = _db_connect()
    try:
        cur = con.execute(
            """INSERT INTO nvr_events
               (ts, code, action, event_index, channel_number, source, snapshot_path, clip_path, extra_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                event["ts"],
                event["code"],
                event["action"],
                int(event.get("index") or 0),
                event.get("channel_number"),
                event.get("source") or "nvr",
                snapshot_path,
                None,
                json.dumps(extra) if extra else None,
            ),
        )
        con.commit()
        event_id = cur.lastrowid
    finally:
        con.close()
    return event_id


def _update_nvr_event_clip_path(event_id, clip_path):
    con = _db_connect()
    try:
        con.execute("UPDATE nvr_events SET clip_path=? WHERE id=?", (clip_path, event_id))
        con.commit()
    finally:
        con.close()


def _event_clip_source_url(path_name):
    safe_path = (path_name or "").strip().strip("/")
    if not safe_path:
        return None
    return f"rtsp://{MTX_RTSP_HOST}:{MTX_RTSP_PORT}/{safe_path}"


def _capture_nvr_event_clip(event_id, path_name):
    source_url = _event_clip_source_url(path_name)
    if not source_url:
        return

    os.makedirs(NVR_EVENT_CLIP_DIR, exist_ok=True)
    clip_path = os.path.join(NVR_EVENT_CLIP_DIR, f"event_{event_id}_{uuid4().hex[:8]}.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        source_url,
        "-t",
        str(NVR_EVENT_CLIP_SECS),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        clip_path,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=NVR_EVENT_CLIP_SECS + 20)
    except Exception as exc:
        if os.path.exists(clip_path):
            os.remove(clip_path)
        print(f"[NVR] clip capture failed for event {event_id}: {exc}", flush=True)
        return

    _update_nvr_event_clip_path(event_id, clip_path)
    with _nvr_lock:
        _nvr_bump_locked()


def _start_event_clip_capture(event_id, path_name):
    if not path_name:
        return
    threading.Thread(
        target=_capture_nvr_event_clip,
        args=(event_id, path_name),
        daemon=True,
        name=f"nvr-clip-{event_id}",
    ).start()

def _claim_capture_slot(channel_number, code):
    """True kalau event ini boleh menghasilkan snapshot + clip.

    VideoMotion/SmartMotionHuman terus dikirim NVR selama masih ada gerakan —
    satu kejadian bisa jadi puluhan event dalam semenit, dan tiap event berarti
    satu clip belasan detik lagi. Gate ini membuat satu channel maksimal
    menghasilkan 1 media per NVR_EVENT_NOISY_COOLDOWN_SECS untuk kode tersebut.
    Event-nya sendiri tetap dicatat penuh di log, hanya medianya yang dilewat."""
    if NVR_EVENT_NOISY_COOLDOWN_SECS <= 0 or code not in NVR_EVENT_NOISY_CODES:
        return True
    key = (channel_number, code)
    now = time.monotonic()
    with _capture_cooldown_lock:
        if now - _capture_cooldown.get(key, 0.0) < NVR_EVENT_NOISY_COOLDOWN_SECS:
            return False
        _capture_cooldown[key] = now
    return True


def _media_inventory():
    """(total_bytes, [(mtime, size, path), ...] urut dari yang paling lama)."""
    total = 0
    files = []
    for directory in _MEDIA_DIRS:
        try:
            entries = os.scandir(directory)
        except (FileNotFoundError, NotADirectoryError):
            continue
        with entries:
            for entry in entries:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                total += st.st_size
                files.append((st.st_mtime, st.st_size, entry.path))
    files.sort(key=lambda item: item[0])
    return total, files


def _forget_missing_media():
    """Kosongkan kolom path yang filenya sudah tidak ada, supaya UI tidak
    menampilkan thumbnail / tombol playback yang sudah pasti 404.

    Dicocokkan lewat os.path.exists, bukan lewat daftar path yang barusan
    dihapus: satu kali scan per tabel (UPDATE-nya lewat primary key), dan
    sekalian membereskan file yang hilang karena dihapus manual di server."""
    con = _db_connect()
    try:
        for table, cols in (("nvr_events", ("snapshot_path", "clip_path")),
                            ("detection_events", ("snapshot_path",))):
            for col in cols:
                stale = [
                    (row["id"],)
                    for row in con.execute(
                        f"SELECT id, {col} FROM {table} WHERE {col} IS NOT NULL")
                    if not os.path.exists(row[col])
                ]
                if stale:
                    con.executemany(f"UPDATE {table} SET {col}=NULL WHERE id=?", stale)
        con.commit()
    finally:
        con.close()


def _trim_event_rows():
    """Buang baris event tertua beserta filenya kalau tabel melewati
    EVENT_DB_MAX_ROWS."""
    if EVENT_DB_MAX_ROWS <= 0:
        return 0
    removed = 0
    con = _db_connect()
    try:
        for table, cols in (("nvr_events", ("snapshot_path", "clip_path")),
                            ("detection_events", ("snapshot_path",))):
            excess = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] - EVENT_DB_MAX_ROWS
            if excess <= 0:
                continue
            rows = con.execute(
                f"SELECT id, {', '.join(cols)} FROM {table} ORDER BY id ASC LIMIT ?", (excess,)
            ).fetchall()
            if not rows:
                continue
            for row in rows:
                for col in cols:
                    path = row[col]
                    if not path:
                        continue
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            con.execute(f"DELETE FROM {table} WHERE id <= ?", (rows[-1]["id"],))
            removed += len(rows)
        con.commit()
    finally:
        con.close()
    return removed


def _media_janitor_cycle():
    trimmed_rows = _trim_event_rows()
    total, files = _media_inventory()
    budget = int(MEDIA_MAX_GB * 1024 ** 3) if MEDIA_MAX_GB > 0 else 0
    cutoff = (time.time() - MEDIA_RETENTION_DAYS * 86400) if MEDIA_RETENTION_DAYS > 0 else None

    deleted, freed = [], 0
    for mtime, size, path in files:  # sudah urut: yang paling lama dibuang duluan
        too_old = cutoff is not None and mtime < cutoff
        over_budget = budget > 0 and (total - freed) > budget
        if not too_old and not over_budget:
            break  # sisanya lebih baru dan sudah muat di budget
        try:
            os.remove(path)
        except OSError:
            continue
        freed += size
        deleted.append(path)

    if deleted:
        _forget_missing_media()

    dropped = set(deleted)
    remaining = [item for item in files if item[2] not in dropped]
    _media_usage.update({
        "bytes": total - freed,
        "files": len(remaining),
        "budget_bytes": budget,
        "retention_days": MEDIA_RETENTION_DAYS,
        "oldest": (datetime.utcfromtimestamp(remaining[0][0]).strftime("%Y-%m-%dT%H:%M:%SZ")
                   if remaining else None),
        "last_run": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deleted_last": len(deleted),
        "freed_last": freed,
        "rows_trimmed_last": trimmed_rows,
    })

    if deleted or trimmed_rows:
        print(f"[JANITOR] hapus {len(deleted)} file ({freed / 1024 ** 3:.2f} GB), "
              f"pangkas {trimmed_rows} baris event — sisa "
              f"{(total - freed) / 1024 ** 3:.2f} GB / {MEDIA_MAX_GB:g} GB", flush=True)
        with _nvr_lock:
            _nvr_bump_locked()


def _media_janitor_worker():
    time.sleep(30)
    last_error = None
    while True:
        try:
            _media_janitor_cycle()
            last_error = None
        except Exception as e:
            msg = str(e)[:150]
            if msg != last_error:
                print(f"[JANITOR] gagal: {msg}", flush=True)
                last_error = msg
        time.sleep(MEDIA_JANITOR_SECS)


# ── DB helpers ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db: db.close()


def ensure_camera_columns(con):
    existing = {row[1] for row in con.execute("PRAGMA table_info(cameras)")}
    if "rtsp_url" not in existing:
        con.execute("ALTER TABLE cameras ADD COLUMN rtsp_url TEXT")
    if "channel" not in existing:
        con.execute("ALTER TABLE cameras ADD COLUMN channel INTEGER")
    if "ai_enabled" not in existing:
        con.execute("ALTER TABLE cameras ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 1")
    if "ptz_supported" not in existing:
        # Default 0 — tidak semua channel NVR punya motor PTZ (speed dome).
        # User menyalakan manual per kamera lewat modal edit.
        con.execute("ALTER TABLE cameras ADD COLUMN ptz_supported INTEGER NOT NULL DEFAULT 0")


def ensure_detection_columns(con):
    existing = {row[1] for row in con.execute("PRAGMA table_info(detection_events)")}
    if "alarm_triggered" not in existing:
        con.execute("ALTER TABLE detection_events ADD COLUMN alarm_triggered INTEGER NOT NULL DEFAULT 0")
    if "snapshot_path" not in existing:
        con.execute("ALTER TABLE detection_events ADD COLUMN snapshot_path TEXT")


def ensure_nvr_event_columns(con):
    existing = {row[1] for row in con.execute("PRAGMA table_info(nvr_events)")}
    if "clip_path" not in existing:
        con.execute("ALTER TABLE nvr_events ADD COLUMN clip_path TEXT")


def build_rtsp_source(rtsp_url, channel, quality="source"):
    base = rtsp_url.strip()
    if not base:
        raise ValueError("rtsp_url is required")

    if "://" not in base:
        base = f"rtsps://{base}"

    channel_str = str(int(channel))
    if "{channel}" in base:
        return base.replace("{channel}", channel_str)

    parts = urlsplit(base)
    if parts.scheme not in {"rtsp", "rtsps", "rtsp+http", "rtsps+http", "rtsp+ws", "rtsps+ws"}:
        raise ValueError("unsupported rtsp_url scheme")

    pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "channel"]
    # Kualitas 'sub' → paksa substream Dahua (subtype=1) bila URL punya param subtype
    if quality == "sub":
        pairs = [(k, "1" if k == "subtype" else v) for k, v in pairs]
    pairs.insert(0, ("channel", channel_str))

    return urlunsplit(parts._replace(query=urlencode(pairs)))


def _get_stream_quality():
    val = _db_setting('stream_quality') or 'source'
    return val if val in STREAM_QUALITIES else 'source'


# ── Normalisasi URL RTSP kamera custom ───────────────────────────────────────
# Beberapa kamera Dahua (mis. DH-P5AE-PV) MEWAJIBKAN TLS di port 554 dan
# menolak param query unicast/proto/tls, sementara NVR justru toleran keduanya.
# Supaya user tidak perlu tahu quirk per kamera saat menambah channel, probe
# beberapa varian URL (DESCRIBE + digest auth) dan pakai yang dijawab kamera.

def _strip_picky_params(url):
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in {"unicast", "proto", "tls"}]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


def _flip_rtsp_scheme(url):
    if url.startswith("rtsps://"):
        return "rtsp://" + url[len("rtsps://"):]
    if url.startswith("rtsp://"):
        return "rtsps://" + url[len("rtsp://"):]
    return url


def _rtsp_describe_ok(url, timeout=4):
    """DESCRIBE satu kali (plaintext atau TLS sesuai scheme). True bila 200 OK.
    Request-line selalu memakai scheme rtsp:// — NVR menerima itu juga lewat
    TLS, sedangkan kamera strict justru tidak membalas scheme rtsps://."""
    import hashlib
    import socket as _socket
    import ssl as _ssl

    parts = urlsplit(url)
    if parts.scheme not in {"rtsp", "rtsps"}:
        return False
    host = parts.hostname
    port = parts.port or 554
    user = unquote(parts.username or "")
    passwd = unquote(parts.password or "")
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    uri = f"rtsp://{host}:{port}{path}"

    try:
        sock = _socket.create_connection((host, port), timeout=timeout)
        if parts.scheme == "rtsps":
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            # Kamera Dahua lama hanya punya cipher RSA-kex non-PFS
            ctx.set_ciphers("ALL:@SECLEVEL=0")
            sock = ctx.wrap_socket(sock)

        def request(extra=""):
            sock.sendall(
                f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n{extra}\r\n".encode()
            )
            sock.settimeout(timeout)
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            return data.decode(errors="replace")

        resp = request()
        if resp.startswith("RTSP/1.0 200"):
            return True
        m = re.search(r'realm="([^"]+)"', resp)
        n = re.search(r'nonce="([^"]+)"', resp)
        if not (m and n and user):
            return False
        realm, nonce = m.group(1), n.group(1)
        ha1 = hashlib.md5(f"{user}:{realm}:{passwd}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
        digest = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        auth = (
            f'Authorization: Digest username="{user}", realm="{realm}", '
            f'nonce="{nonce}", uri="{uri}", response="{digest}"\r\n'
        )
        return request(auth).startswith("RTSP/1.0 200")
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _normalize_rtsp_source(source_url):
    """Kembalikan varian URL pertama yang dijawab kamera; fallback apa adanya.
    Urutan disusun supaya kredensial benar tidak pernah 'gagal login' berulang
    (lockout Dahua): varian paling mungkin dicoba lebih dulu."""
    flipped = _flip_rtsp_scheme(source_url)
    variants = []
    for candidate in (
        source_url,
        _strip_picky_params(source_url),
        _strip_picky_params(flipped),
        flipped,
    ):
        if candidate not in variants:
            variants.append(candidate)
    for candidate in variants:
        if _rtsp_describe_ok(candidate):
            if candidate != source_url:
                print(f"[STREAM] URL dinormalisasi: {source_url!r} -> {candidate!r}", flush=True)
            return candidate
    return source_url


def extract_custom_path_name(stream_url):
    path = urlsplit(stream_url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0].startswith("custom_"):
        return parts[0]
    return None


def _kill_orphan_custom_streams(path_name):
    current_pid = os.getpid()
    for pid_name in os.listdir('/proc'):
        if not pid_name.isdigit():
            continue
        pid = int(pid_name)
        if pid == current_pid:
            continue
        try:
            with open(f'/proc/{pid_name}/cmdline', 'rb') as fh:
                raw = fh.read()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b'\x00', b' ').decode('utf-8', errors='ignore')
        if path_name not in cmdline:
            continue
        if 'start_custom_stream.sh' not in cmdline and 'ffmpeg' not in cmdline:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            continue


def stop_custom_stream(path_name):
    entry = CUSTOM_STREAM_PROCS.pop(path_name, None)
    if entry:
        proc = entry["proc"]
        log_handle = entry["log"]
        if proc.poll() is None:
            proc.terminate()
        log_handle.close()
    _kill_orphan_custom_streams(path_name)


def launch_custom_stream(path_name, rtsp_url, channel):
    stop_custom_stream(path_name)
    quality = _get_stream_quality()
    source_url = _normalize_rtsp_source(build_rtsp_source(rtsp_url, channel, quality=quality))
    log_handle = open(f"/tmp/{path_name}.log", "ab")
    env = dict(os.environ, STREAM_QUALITY=quality)
    proc = subprocess.Popen(
        [CUSTOM_STREAM_SCRIPT, source_url, path_name],
        cwd=APP_DIR,
        stdout=log_handle,
        stderr=log_handle,
        env=env,
    )
    CUSTOM_STREAM_PROCS[path_name] = {"proc": proc, "log": log_handle}


def restore_custom_streams(con):
    rows = con.execute(
        "SELECT stream_url, rtsp_url, channel FROM cameras WHERE builtin=0 AND rtsp_url IS NOT NULL AND rtsp_url != '' AND channel IS NOT NULL"
    ).fetchall()
    for row in rows:
        path_name = extract_custom_path_name(row[0])
        if not path_name:
            continue
        try:
            launch_custom_stream(path_name, row[1], row[2])
        except Exception as e:
            print(f"[STREAM] restore failed for {path_name}: {e}", flush=True)


@atexit.register
def stop_all_custom_streams():
    for path_name in list(CUSTOM_STREAM_PROCS.keys()):
        stop_custom_stream(path_name)


def _builtin_channel_count(con):
    row = con.execute("SELECT value FROM settings WHERE key='builtin_channel_count'").fetchone()
    try:
        count = int(row[0]) if row else 4
    except (TypeError, ValueError):
        count = 4
    return max(0, min(count, 64))


def sync_camera_rows(con):
    channel_count = _builtin_channel_count(con)
    # Mapping baris builtin → nomor channel mengikuti kolom channel (stabil),
    # BUKAN sort_order — sort_order adalah urutan tampil yang bebas diatur user
    # dan tidak boleh direset saat backend restart.
    builtin_rows = con.execute(
        "SELECT id FROM cameras WHERE builtin=1 "
        "ORDER BY CASE WHEN channel IS NULL THEN 1 ELSE 0 END, channel, id"
    ).fetchall()

    for channel in range(1, channel_count + 1):
        stream_url = _public_stream_url(f"ch{channel}")
        if channel <= len(builtin_rows):
            # Nama kamera & urutan tampil milik user (editable) — jangan ditimpa,
            # cukup sinkronkan stream_url/nomor channel dengan runtime saat ini.
            con.execute(
                "UPDATE cameras SET stream_url=?, channel=? WHERE id=?",
                (stream_url, channel, builtin_rows[channel - 1][0]),
            )
        else:
            con.execute(
                "INSERT INTO cameras (name, stream_url, sort_order, builtin, rtsp_url, channel) VALUES (?,?,?,?,?,?)",
                (f"Camera {channel}", stream_url, channel, 1, None, channel),
            )

    # Jika jumlah channel builtin dikurangi, hapus baris berlebih
    for extra in builtin_rows[channel_count:]:
        con.execute("DELETE FROM cameras WHERE id=?", (extra[0],))

    custom_rows = con.execute(
        "SELECT id, stream_url FROM cameras WHERE builtin=0"
    ).fetchall()
    for row in custom_rows:
        row_id, stream_url = row[0], row[1]
        normalized = _rewrite_stream_url_for_current_runtime(stream_url)
        if normalized != stream_url:
            con.execute("UPDATE cameras SET stream_url=? WHERE id=?", (normalized, row_id))

def init_db():
    """Create table and seed default 4 channels if DB is new."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS cameras (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            stream_url TEXT    NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            builtin    INTEGER NOT NULL DEFAULT 0
        )
    """)
    ensure_camera_columns(con)
    # New tables for AI detection feature
    con.execute("""
        CREATE TABLE IF NOT EXISTS zones (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id   INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            points_json TEXT    NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS known_faces (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            photo_path TEXT,
            created_at TEXT    NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS detection_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              TEXT    NOT NULL,
            channel_id      TEXT    NOT NULL,
            camera_name     TEXT    NOT NULL DEFAULT '',
            event_type      TEXT    NOT NULL,
            zone_name       TEXT,
            person_name     TEXT,
            confidence      REAL,
            extra_json      TEXT,
            alarm_triggered INTEGER NOT NULL DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS nvr_events (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            ts             TEXT    NOT NULL,
            code           TEXT    NOT NULL,
            action         TEXT    NOT NULL,
            event_index    INTEGER NOT NULL DEFAULT 0,
            channel_number INTEGER,
            source         TEXT    NOT NULL DEFAULT 'nvr',
            snapshot_path  TEXT,
            clip_path      TEXT,
            extra_json     TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS login_log (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            ts       TEXT    NOT NULL,
            username TEXT    NOT NULL,
            success  INTEGER NOT NULL,
            ip       TEXT,
            blocked  INTEGER NOT NULL DEFAULT 0
        )
    """)
    existing_ll = {row[1] for row in con.execute("PRAGMA table_info(login_log)")}
    if "blocked" not in existing_ll:
        con.execute("ALTER TABLE login_log ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0")
    if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        con.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
            (ADMIN_USER, generate_password_hash(ADMIN_PASS), datetime.utcnow().isoformat()),
        )
        print(
            f"[AUTH] Seeded default user '{ADMIN_USER}' — GANTI PASSWORD SEGERA "
            f"lewat menu Konfigurasi > Users.",
            flush=True,
        )
    # Seed default settings (env hanya jadi nilai awal; selanjutnya dikelola via UI)
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('mode', 'home')")
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ai_global_enabled', '1')")
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('login_whitelist', ?)", (ALLOWED_LOGIN_CIDRS,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_host', ?)", (DVR_HOST,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_http_port', ?)", (str(DVR_HTTP_PORT),))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('builtin_channel_count', ?)", (os.getenv("NVR_CHANNELS", "4"),))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_stream_user', ?)" , (DVR_USER,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_stream_pass', ?)" , (DVR_PASS,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_event_user', ?)" , (DVR_EVENT_USER,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_event_pass', ?)" , (DVR_EVENT_PASS,))
    ensure_detection_columns(con)
    ensure_nvr_event_columns(con)
    sync_camera_rows(con)
    con.commit()
    con.close()
    # Latar belakang — bukan blocking di sini. Tiap kamera custom bisa butuh
    # sampai ~4 percobaan probe x beberapa detik (_normalize_rtsp_source) untuk
    # cari varian URL yang benar; sekuensial dan makin lama makin banyak kamera
    # custom yang terdaftar. Kalau ini dijalankan sinkron di sini (proses impor
    # modul, sebelum gunicorn worker selesai boot), total waktunya bisa lebih
    # lama dari worker timeout gunicorn (default 30s) → worker dibunuh terus,
    # reboot berulang, dan backend TIDAK PERNAH selesai start (semua request
    # menggantung selamanya, termasuk /api/login).
    threading.Thread(target=_restore_custom_streams_bg, daemon=True, name="restore-streams").start()


def _restore_custom_streams_bg():
    con = sqlite3.connect(DB_PATH)
    try:
        restore_custom_streams(con)
    finally:
        con.close()

# ── Auth Routes ───────────────────────────────────────────────
# Satu tingkat akses saja (tidak ada admin vs user biasa) — cocok untuk
# dashboard rumah dengan sedikit akun keluarga, bukan multi-tenant.

def _client_ip():
    return request.headers.get('X-Real-IP') or request.remote_addr or ''


def _login_whitelist_networks():
    """Gabungan whitelist dari DB (editable via UI) + env EXTRA_LOGIN_CIDRS
    (pintu darurat anti-lockout). Entri tidak valid dilewati diam-diam."""
    import ipaddress
    raw = _db_setting('login_whitelist', ALLOWED_LOGIN_CIDRS)
    combined = f"{raw},{EXTRA_LOGIN_CIDRS}"
    nets = []
    for part in combined.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            pass
    return nets


def _ip_login_allowed(ip_str):
    import ipaddress
    nets = _login_whitelist_networks()
    if not nets:
        # Whitelist kosong total = tidak ada yang bisa login — anggap salah
        # konfigurasi dan izinkan semua daripada mengunci seluruh akses.
        return True
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(addr in net for net in nets)


@app.route('/api/login', methods=['POST'])
def login_route():
    body = request.get_json(silent=True) or {}
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''

    db = get_db()
    ip = _client_ip()
    if not _ip_login_allowed(ip):
        db.execute(
            "INSERT INTO login_log (ts, username, success, ip, blocked) VALUES (?,?,0,?,1)",
            (datetime.utcnow().isoformat(), username or '(kosong)', ip),
        )
        db.commit()
        print(f"[AUTH] Login DIBLOKIR dari {ip} (di luar whitelist) user='{username}'", flush=True)
        return jsonify({'error': 'Akses login dari jaringan ini tidak diizinkan'}), 403

    row = db.execute(
        "SELECT id, username, password_hash FROM users WHERE username=?", (username,)
    ).fetchone()
    ok = bool(row) and check_password_hash(row['password_hash'], password)

    db.execute(
        "INSERT INTO login_log (ts, username, success, ip) VALUES (?,?,?,?)",
        (datetime.utcnow().isoformat(), username or '(kosong)', 1 if ok else 0, ip),
    )
    db.commit()

    if not ok:
        return jsonify({'error': 'Username atau password salah'}), 401

    session.clear()
    session['user_id'] = row['id']
    session['username'] = row['username']
    session.permanent = True
    return jsonify({'username': row['username']})


@app.route('/api/logout', methods=['POST'])
def logout_route():
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/session', methods=['GET'])
def session_status():
    if session.get('user_id'):
        session.permanent = True
        return jsonify({'authenticated': True, 'username': session.get('username')})
    return jsonify({'authenticated': False})


@app.route('/api/users', methods=['GET'])
def list_users():
    rows = get_db().execute(
        "SELECT id, username, created_at FROM users ORDER BY id"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/users', methods=['POST'])
def create_user():
    body = request.get_json(silent=True) or {}
    username = (body.get('username') or '').strip()
    password = body.get('password') or ''
    if not username:
        return jsonify({'error': 'Username tidak boleh kosong'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password minimal 6 karakter'}), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        return jsonify({'error': 'Username sudah dipakai'}), 400
    cur = db.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?,?,?)",
        (username, generate_password_hash(password), datetime.utcnow().isoformat()),
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'username': username}), 201


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
def delete_user(user_id):
    db = get_db()
    remaining = db.execute("SELECT COUNT(*) c FROM users").fetchone()['c']
    if remaining <= 1:
        return jsonify({'error': 'Tidak bisa hapus satu-satunya user yang tersisa'}), 400
    if session.get('user_id') == user_id:
        return jsonify({'error': 'Tidak bisa hapus akun yang sedang login'}), 400
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return '', 204


@app.route('/api/login-log', methods=['GET'])
def get_login_log():
    try:
        limit = min(int(request.args.get('limit', 100)), 500)
    except (TypeError, ValueError):
        limit = 100
    rows = get_db().execute(
        "SELECT id, ts, username, success, ip, blocked FROM login_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/login-whitelist', methods=['GET'])
def get_login_whitelist():
    return jsonify({
        'cidrs': _db_setting('login_whitelist', ALLOWED_LOGIN_CIDRS),
        'extra_env': EXTRA_LOGIN_CIDRS,
    })


@app.route('/api/login-whitelist', methods=['POST'])
def set_login_whitelist():
    import ipaddress
    body = request.get_json(silent=True) or {}
    raw = (body.get('cidrs') or '').strip()
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if not parts:
        return jsonify({'error': 'Whitelist tidak boleh kosong'}), 400
    for part in parts:
        try:
            ipaddress.ip_network(part, strict=False)
        except ValueError:
            return jsonify({'error': f'CIDR tidak valid: {part}'}), 400
    # Tolak penyimpanan yang akan mengunci admin yang sedang menyimpannya.
    ip = _client_ip()
    try:
        addr = ipaddress.ip_address(ip)
        nets = [ipaddress.ip_network(p, strict=False) for p in parts]
        extra = [ipaddress.ip_network(p.strip(), strict=False)
                 for p in EXTRA_LOGIN_CIDRS.split(',') if p.strip()]
        if not any(addr in n for n in nets + extra):
            return jsonify({'error': f'Ditolak: IP kamu sendiri ({ip}) tidak masuk whitelist ini — kamu akan terkunci'}), 400
    except ValueError:
        pass
    value = ','.join(parts)
    _set_db_setting('login_whitelist', value)
    print(f"[AUTH] Login whitelist diubah menjadi: {value}", flush=True)
    return jsonify({'cidrs': value})


# ── Routes ──────────────────────────────────────────────────

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    rows = get_db().execute(
        "SELECT id, name, stream_url, builtin, rtsp_url, channel, ai_enabled, ptz_supported FROM cameras ORDER BY sort_order, id"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/mtx-status', methods=['GET'])
def get_mtx_status():
    paths_payload = _mtx_api_request("GET", "/v3/paths/list")
    if paths_payload is None:
        return jsonify({'ok': False, 'error': 'MediaMTX control API unavailable'}), 503

    sessions_payload = _mtx_api_request("GET", "/v3/rtspsessions/list") or {"items": []}
    return jsonify({
        'ok': True,
        'paths': paths_payload.get('items') or [],
        'rtsp_sessions': sessions_payload.get('items') or [],
    })


@app.route('/api/cameras', methods=['POST'])
def add_camera():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()

    if not name:
        return jsonify({'error': 'name is required'}), 400

    stream_url = (body.get('stream_url') or '').strip()
    rtsp_url = (body.get('rtsp_url') or '').strip()
    channel_raw = str(body.get('channel') or '').strip()

    custom_rtsp_url = None
    custom_channel = None
    path_name = None

    if rtsp_url or channel_raw:
        if not rtsp_url:
            return jsonify({'error': 'rtsp_url is required'}), 400
        if not channel_raw.isdigit() or int(channel_raw) <= 0:
            return jsonify({'error': 'channel must be a positive integer'}), 400

        custom_rtsp_url = rtsp_url
        custom_channel = int(channel_raw)
        path_name = f"custom_{uuid4().hex[:10]}"
        stream_url = f"{BASE_URL}/{path_name}/index.m3u8"
    else:
        if not stream_url.startswith('http'):
            return jsonify({'error': 'stream_url must start with http'}), 400

    db = get_db()
    with _camera_write_lock:
        if custom_rtsp_url and custom_channel:
            existing = db.execute(
                """SELECT id, name, stream_url, builtin FROM cameras
                   WHERE builtin=0 AND name=? AND rtsp_url=? AND channel=?
                   ORDER BY id DESC LIMIT 1""",
                (name, custom_rtsp_url, custom_channel)
            ).fetchone()
            if existing:
                return jsonify(dict(existing)), 200

        ptz_supported = 1 if body.get('ptz_supported') else 0
        max_order = db.execute("SELECT COALESCE(MAX(sort_order),0) FROM cameras").fetchone()[0]
        cur = db.execute(
            "INSERT INTO cameras (name, stream_url, sort_order, builtin, rtsp_url, channel, ptz_supported) VALUES (?,?,?,0,?,?,?)",
            (name, stream_url, max_order + 1, custom_rtsp_url, custom_channel, ptz_supported)
        )
        db.commit()
        camera_id = cur.lastrowid

    stream_warning = None
    if path_name and custom_rtsp_url and custom_channel:
        try:
            launch_custom_stream(path_name, custom_rtsp_url, custom_channel)
        except Exception as e:
            stream_warning = str(e)[:160]
            print(f"[STREAM] launch failed for {path_name}: {e}", flush=True)

    payload = {'id': camera_id, 'name': name, 'stream_url': stream_url, 'builtin': 0,
               'ptz_supported': ptz_supported}
    if stream_warning:
        payload['stream_warning'] = stream_warning
    return jsonify(payload), 201


@app.route('/api/cameras/<int:cam_id>', methods=['DELETE'])
def delete_camera(cam_id):
    db = get_db()
    row = db.execute("SELECT builtin, stream_url FROM cameras WHERE id=?", (cam_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    if row['builtin']:
        return jsonify({'error': 'cannot delete built-in camera'}), 403
    path_name = extract_custom_path_name(row['stream_url'])
    if path_name:
        stop_custom_stream(path_name)
    db.execute("DELETE FROM cameras WHERE id=?", (cam_id,))
    db.commit()
    return '', 204


@app.route('/api/cameras/<int:cam_id>', methods=['PATCH'])
def update_camera(cam_id):
    db = get_db()
    row = db.execute(
        "SELECT id, builtin, stream_url, rtsp_url, channel FROM cameras WHERE id=?", (cam_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404

    body = request.get_json(silent=True) or {}
    fields, vals = [], []
    if 'name' in body:
        name = (body['name'] or '').strip()
        if not name:
            return jsonify({'error': 'name cannot be empty'}), 400
        fields.append('name=?'); vals.append(name)
    if 'ai_enabled' in body:
        fields.append('ai_enabled=?'); vals.append(1 if body['ai_enabled'] else 0)
    if 'ptz_supported' in body:
        fields.append('ptz_supported=?'); vals.append(1 if body['ptz_supported'] else 0)
    if 'stream_url' in body:
        if row['builtin']:
            return jsonify({'error': 'stream_url kamera built-in dikelola sistem'}), 403
        url = (body['stream_url'] or '').strip()
        if not url.startswith('http'):
            return jsonify({'error': 'stream_url must start with http'}), 400
        fields.append('stream_url=?'); vals.append(url)

    # Edit sumber RTSP / nomor channel untuk kamera custom → relaunch publisher
    new_rtsp = None
    new_channel = None
    if 'rtsp_url' in body or 'channel' in body:
        if row['builtin']:
            return jsonify({'error': 'sumber RTSP kamera built-in diatur lewat konfigurasi NVR'}), 403
        new_rtsp = (body.get('rtsp_url') if 'rtsp_url' in body else row['rtsp_url']) or ''
        new_rtsp = new_rtsp.strip()
        channel_raw = body.get('channel') if 'channel' in body else row['channel']
        channel_str = str(channel_raw if channel_raw is not None else '').strip()
        if not new_rtsp:
            return jsonify({'error': 'rtsp_url is required'}), 400
        if not channel_str.isdigit() or int(channel_str) <= 0:
            return jsonify({'error': 'channel must be a positive integer'}), 400
        new_channel = int(channel_str)
        fields.append('rtsp_url=?'); vals.append(new_rtsp)
        fields.append('channel=?'); vals.append(new_channel)

    if fields:
        vals.append(cam_id)
        with _camera_write_lock:
            db.execute(f"UPDATE cameras SET {', '.join(fields)} WHERE id=?", vals)
            db.commit()

    stream_warning = None
    if new_rtsp and new_channel:
        path_name = extract_custom_path_name(row['stream_url'])
        if path_name:
            try:
                launch_custom_stream(path_name, new_rtsp, new_channel)
            except Exception as e:
                stream_warning = str(e)[:160]
                print(f"[STREAM] relaunch failed for {path_name}: {e}", flush=True)

    updated = db.execute(
        "SELECT id, name, stream_url, builtin, rtsp_url, channel, ai_enabled, ptz_supported FROM cameras WHERE id=?",
        (cam_id,),
    ).fetchone()
    payload = dict(updated)
    if stream_warning:
        payload['stream_warning'] = stream_warning
    return jsonify(payload)


@app.route('/api/cameras/reorder', methods=['POST'])
def reorder_cameras():
    """Simpan urutan tampil kamera. Body: {"order": [id, id, ...]}."""
    body = request.get_json(silent=True) or {}
    order = body.get('order')
    if not isinstance(order, list) or not order or not all(isinstance(i, int) for i in order):
        return jsonify({'error': 'order must be a non-empty array of camera ids'}), 400

    db = get_db()
    known_ids = {r['id'] for r in db.execute("SELECT id FROM cameras").fetchall()}
    unknown = [i for i in order if i not in known_ids]
    if unknown:
        return jsonify({'error': f'unknown camera ids: {unknown}'}), 400

    with _camera_write_lock:
        for position, cam_id in enumerate(order, start=1):
            db.execute("UPDATE cameras SET sort_order=? WHERE id=?", (position, cam_id))
        db.commit()

    rows = db.execute(
        "SELECT id, name, stream_url, builtin, rtsp_url, channel, ai_enabled, ptz_supported FROM cameras ORDER BY sort_order, id"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/stream-status', methods=['GET'])
def stream_status():
    rows = get_db().execute(
        "SELECT id, name, stream_url, builtin FROM cameras ORDER BY sort_order, id"
    ).fetchall()
    mtx_paths = _mtx_paths_map()

    def status_check_url(stream_url):
        # Relative URL (e.g. /ch1/index.m3u8) — proxy via nginx, tapi backend
        # perlu akses langsung ke mediamtx pakai INTERNAL_HLS_URL.
        if stream_url.startswith('/'):
            if INTERNAL_HLS_URL:
                return INTERNAL_HLS_URL + stream_url
            return None
        parts = urlsplit(stream_url)
        if parts.hostname in {"localhost", "127.0.0.1"}:
            if INTERNAL_HLS_URL:
                query = f"?{parts.query}" if parts.query else ""
                return f"{INTERNAL_HLS_URL}{parts.path}{query}"
            port = f":{parts.port}" if parts.port else ""
            return urlunsplit(parts._replace(netloc=f"host.docker.internal{port}"))
        return stream_url

    def check(row):
        try:
            path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]
            if path_seg in mtx_paths:
                path_info = mtx_paths[path_seg]
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'online': bool(path_info.get('ready')),
                    'builtin': bool(row['builtin']),
                }

            check_url = status_check_url(row['stream_url'])
            if not check_url:
                return {'id': row['id'], 'name': row['name'], 'online': False, 'builtin': bool(row['builtin'])}
            res = urllib.request.urlopen(check_url, timeout=3)
            online = res.status == 200
        except Exception:
            online = False
        return {'id': row['id'], 'name': row['name'], 'online': online, 'builtin': bool(row['builtin'])}

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(check, [dict(r) for r in rows]))

    return jsonify(results)


@app.route('/api/stream-restart/<int:cam_id>', methods=['POST'])
def restart_stream(cam_id):
    row = get_db().execute(
        "SELECT id, builtin, stream_url, rtsp_url, channel FROM cameras WHERE id=?", (cam_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404

    if row['builtin']:
        # Kick publisher via MediaMTX API — mediamtx respawns it (runOnInit/runOnDemand).
        # Fallback pkill hanya berguna saat backend & mediamtx berjalan di host yang sama.
        path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]  # e.g. "ch1"
        if not _mtx_kick_publisher(path_seg):
            _restart_host_rtsp_publisher(path_seg)
    else:
        path_name = extract_custom_path_name(row['stream_url'])
        if path_name and row['rtsp_url'] and row['channel']:
            stop_custom_stream(path_name)
            launch_custom_stream(path_name, row['rtsp_url'], row['channel'])

    return jsonify({'ok': True})


@app.route('/api/stream-restart-all', methods=['POST'])
def restart_all_streams():
    rows = get_db().execute(
        "SELECT id, builtin, stream_url, rtsp_url, channel FROM cameras"
    ).fetchall()
    for row in rows:
        if row['builtin']:
            path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]
            if not _mtx_kick_publisher(path_seg):
                _restart_host_rtsp_publisher(path_seg)
        else:
            path_name = extract_custom_path_name(row['stream_url'])
            if path_name and row['rtsp_url'] and row['channel']:
                stop_custom_stream(path_name)
                launch_custom_stream(path_name, row['rtsp_url'], row['channel'])
    return jsonify({'ok': True})


# ── PTZ Control ─────────────────────────────────────────────────────────────

PTZ_CODES = {
    "Up", "Down", "Left", "Right",
    "LeftUp", "RightUp", "LeftDown", "RightDown",
    "ZoomTele", "ZoomWide", "FocusNear", "FocusFar",
    "IrisLarge", "IrisSmall",
}


def _camera_nvr_channel(row):
    """Tentukan nomor channel NVR sebuah kamera untuk perintah PTZ."""
    if row['channel']:
        return int(row['channel'])
    path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]
    match = re.fullmatch(r'ch(\d+)', path_seg)
    return int(match.group(1)) if match else None


def _camera_ptz_target(row):
    """Tentukan (host, port, kandidat kredensial) tujuan perintah PTZ.

    Kamera custom (punya rtsp_url) → host + kredensial diambil dari sumber
    RTSP-nya sendiri: kamera IP PTZ berdiri sendiri menerima ptz.cgi di IP-nya,
    bukan di NVR. Kredensial NVR tetap dicoba sebagai fallback.
    Kamera built-in → NVR utama dari konfigurasi.
    """
    if row['rtsp_url']:
        parts = urlsplit(row['rtsp_url'])
        if parts.hostname:
            creds = []
            if parts.username:
                creds.append((unquote(parts.username), unquote(parts.password or '')))
            for pair in _nvr_auth_candidates():
                if pair not in creds:
                    creds.append(pair)
            port = int(os.getenv("CAMERA_HTTP_PORT", "80"))
            return parts.hostname, port, creds
    return _get_nvr_host(), _get_nvr_http_port(), _nvr_auth_candidates()


@app.route('/api/cameras/<int:cam_id>/ptz', methods=['POST'])
def camera_ptz(cam_id):
    row = get_db().execute(
        "SELECT id, builtin, stream_url, rtsp_url, channel, ptz_supported FROM cameras WHERE id=?", (cam_id,)
    ).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    if not row['ptz_supported']:
        return jsonify({'error': 'kamera ini tidak ditandai mendukung PTZ'}), 400

    body = request.get_json(silent=True) or {}
    action = body.get('action')
    code = body.get('code')
    if action not in {'start', 'stop'}:
        return jsonify({'error': "action must be 'start' or 'stop'"}), 400
    if code not in PTZ_CODES:
        return jsonify({'error': f'code must be one of {sorted(PTZ_CODES)}'}), 400
    try:
        speed = int(body.get('speed', 4))
    except (TypeError, ValueError):
        speed = 4
    speed = min(max(speed, 1), 8)

    channel = _camera_nvr_channel(row)
    if not channel:
        return jsonify({'error': 'kamera ini tidak punya nomor channel NVR'}), 400

    ptz_host, ptz_port, auth_candidates = _camera_ptz_target(row)
    if not ptz_host:
        return jsonify({'error': 'NVR host belum dikonfigurasi'}), 503

    url = (
        f"http://{ptz_host}:{ptz_port}/cgi-bin/ptz.cgi"
        f"?action={action}&channel={channel}&code={code}&arg1=0&arg2={speed}&arg3=0"
    )
    last_error = 'PTZ request failed'
    for user, passwd in auth_candidates:
        try:
            resp = requests.get(url, auth=HTTPDigestAuth(user, passwd), timeout=(5, 8))
        except Exception as exc:
            last_error = str(exc)[:160]
            continue
        if resp.status_code == 200:
            return jsonify({'ok': True})
        last_error = f"HTTP {resp.status_code}"

    return jsonify({'ok': False, 'error': last_error}), 502


# ── NVR Credential helpers ──────────────────────────────────────────────────

def _db_setting(key, fallback=''):
    """Read a single setting value from DB; returns fallback if not found."""
    try:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        con.close()
        return row[0] if row else fallback
    except Exception:
        return fallback


def _set_db_setting(key, value):
    """Write (upsert) a setting to DB."""
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    con.commit()
    con.close()


def _get_nvr_host():
    """Host NVR aktif — DB dulu, fallback env."""
    return _db_setting('nvr_host') or DVR_HOST


def _get_nvr_http_port():
    try:
        return int(_db_setting('nvr_http_port') or DVR_HTTP_PORT)
    except (TypeError, ValueError):
        return DVR_HTTP_PORT


def _get_nvr_event_creds():
    """Return (user, pass) for NVR event stream — DB first, then env fallback."""
    user   = _db_setting('nvr_event_user') or DVR_EVENT_USER
    passwd = _db_setting('nvr_event_pass') or DVR_EVENT_PASS
    return user, passwd


def _get_nvr_stream_creds():
    """Return (user, pass) used by MediaMTX/ffmpeg for channel 1-4 streams."""
    user   = _db_setting('nvr_stream_user') or DVR_USER
    passwd = _db_setting('nvr_stream_pass') or DVR_PASS
    return user, passwd


def _nvr_auth_candidates():
    creds = []
    seen = set()
    for pair in (_get_nvr_event_creds(), _get_nvr_stream_creds()):
        if pair in seen:
            continue
        seen.add(pair)
        creds.append(pair)
    return creds


def _nvr_cgi_text(path, timeout=8):
    nvr_host = _get_nvr_host()
    if not nvr_host:
        raise RuntimeError('NVR host is not configured')

    last_error = 'NVR request failed'
    url = f"http://{nvr_host}:{_get_nvr_http_port()}{path}"
    for user, passwd in _nvr_auth_candidates():
        try:
            resp = requests.get(url, auth=HTTPDigestAuth(user, passwd), timeout=(5, timeout))
        except Exception as exc:
            last_error = str(exc)
            continue

        if resp.status_code == 200:
            return resp.text

        last_error = f"HTTP {resp.status_code}"

    raise RuntimeError(last_error)


def _parse_dahua_kv_text(raw_text):
    pairs = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        key, value = line.split('=', 1)
        pairs[key.strip()] = value.strip()
    return pairs


def _build_nvr_info_payload():
    system_info = _parse_dahua_kv_text(_nvr_cgi_text('/cgi-bin/magicBox.cgi?action=getSystemInfo'))
    device_type = _parse_dahua_kv_text(_nvr_cgi_text('/cgi-bin/magicBox.cgi?action=getDeviceType'))
    storage_info = _parse_dahua_kv_text(_nvr_cgi_text('/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo'))
    channel_info = _parse_dahua_kv_text(_nvr_cgi_text('/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle'))

    disks = {}
    for key, value in storage_info.items():
        match = re.match(r'list\.info\[0\]\.Detail\[(\d+)\]\.(.+)', key)
        if not match:
            continue
        disk = disks.setdefault(int(match.group(1)), {})
        field = match.group(2)
        disk[field] = value

    disk_rows = []
    for idx in sorted(disks):
        disk = disks[idx]
        try:
            total_bytes = int(float(disk.get('TotalBytes', '0')))
        except ValueError:
            total_bytes = 0
        try:
            used_bytes = int(float(disk.get('UsedBytes', '0')))
        except ValueError:
            used_bytes = 0
        usage_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes > 0 else None
        disk_rows.append({
            'path': disk.get('Path') or f'disk-{idx}',
            'type': disk.get('Type') or '',
            'is_error': disk.get('IsError', 'false').lower() == 'true',
            'total_bytes': total_bytes,
            'used_bytes': used_bytes,
            'usage_percent': usage_percent,
        })

    channels = []
    for key, value in channel_info.items():
        match = re.match(r'table\.ChannelTitle\[(\d+)\]\.Name', key)
        if not match:
            continue
        index = int(match.group(1)) + 1
        channels.append({'index': index, 'name': value})
    channels.sort(key=lambda item: item['index'])

    with _nvr_lock:
        event_status = dict(_nvr_status)

    return {
        'host': _get_nvr_host(),
        'http_port': _get_nvr_http_port(),
        'device': {
            'model': device_type.get('type') or system_info.get('updateSerial') or '',
            'type_code': system_info.get('deviceType') or '',
            'processor': system_info.get('processor') or '',
            'serial_number': system_info.get('serialNumber') or '',
            'update_serial': system_info.get('updateSerial') or '',
        },
        'storage': {
            'state': storage_info.get('list.info[0].State') or '',
            'health_flag': storage_info.get('list.info[0].HealthDataFlag') or '',
            'disks': disk_rows,
        },
        'channels': channels,
        'events': event_status,
    }


def _restart_host_rtsp_publisher(path_seg):
    pkill = shutil.which("pkill")
    if not pkill:
        return False
    subprocess.run([pkill, '-f', f'rtsp://localhost:8554/{path_seg}'], capture_output=True)
    return True


# ── NVR Event Stream ────────────────────────────────────────────────────────

# Exponential backoff state for 403 (account lock / permission denied)
_nvr_backoff   = 120   # current wait seconds; resets on success
_NVR_BACKOFF_MAX = 900  # Dahua RmLock ≤ 850 s; 15 min ensures unlock


def _nvr_event_worker():
    """Background daemon thread: subscribe to Dahua NVR event stream."""
    global _nvr_backoff
    # Initial startup delay: wait before first attempt to avoid hammering NVR
    # immediately on container start (which can trigger account lockout).
    time.sleep(60)
    while True:
        # Re-read host + credentials from DB each reconnect so config changes take effect
        nvr_host = _get_nvr_host()
        if not nvr_host:
            with _nvr_lock:
                _nvr_status["connected"] = False
                _nvr_status["error"] = "NVR host belum dikonfigurasi"
                _nvr_bump_locked()
            time.sleep(30)
            continue
        url = f"http://{nvr_host}:{_get_nvr_http_port()}/cgi-bin/eventManager.cgi?action=attach&codes=[All]&heartbeat=5"
        event_user, event_pass = _get_nvr_event_creds()
        auth = HTTPDigestAuth(event_user, event_pass)
        try:
            with requests.get(url, auth=auth, stream=True,
                              timeout=(15, 60)) as resp:

                if resp.status_code == 401:
                    # Credentials wrong
                    body = resp.text[:200]
                    print(f"[NVR] 401 Unauthorized: {body}", flush=True)
                    with _nvr_lock:
                        _nvr_status["connected"] = False
                        _nvr_status["error"] = "401 Unauthorized – periksa DVR_USER/DVR_PASS"
                        _nvr_bump_locked()
                    time.sleep(_nvr_backoff)
                    _nvr_backoff = min(_nvr_backoff * 2, _NVR_BACKOFF_MAX)
                    continue

                if resp.status_code == 403:
                    body = resp.text[:300].strip()
                    # Parse RmLock from Dahua JSON to get exact lock duration
                    rm_lock = 0
                    try:
                        data = json.loads(body)
                        rm_lock = int(data.get("RmLock", 0))
                    except Exception:
                        pass
                    wait = max(rm_lock + 30, _nvr_backoff) if rm_lock > 0 else _nvr_backoff
                    locked = rm_lock > 0 or "Lock" in body
                    print(f"[NVR] 403 – RmLock={rm_lock}s, backoff {wait}s", flush=True)
                    with _nvr_lock:
                        _nvr_status["connected"] = False
                        _nvr_status["error"] = (
                            f"403 Akun terkunci, tunggu {wait}s (RmLock={rm_lock}s)"
                            if locked else
                            f"403 Akses ditolak (periksa hak akses user 'dashboard')"
                        )
                        _nvr_bump_locked()
                    time.sleep(wait)
                    _nvr_backoff = min(_nvr_backoff * 2, _NVR_BACKOFF_MAX)
                    continue

                if resp.status_code != 200:
                    print(f"[NVR] HTTP {resp.status_code}", flush=True)
                    with _nvr_lock:
                        _nvr_status["connected"] = False
                        _nvr_status["error"] = f"HTTP {resp.status_code}"
                        _nvr_bump_locked()
                    time.sleep(15)
                    continue

                # Connected successfully – reset backoff
                _nvr_backoff = 120
                with _nvr_lock:
                    _nvr_status["connected"] = True
                    _nvr_status["error"] = None
                    _nvr_bump_locked()
                print("[NVR] Event stream connected", flush=True)

                buf = b""
                for chunk in resp.iter_content(chunk_size=2048):
                    if not chunk:
                        continue
                    buf += chunk
                    while b"\r\n\r\n" in buf:
                        sep = buf.find(b"\r\n\r\n")
                        buf = buf[sep + 4:]
                        boundary = buf.find(b"--myboundary")
                        if boundary == -1:
                            break
                        payload = buf[:boundary].decode("utf-8", errors="replace").strip()
                        buf = buf[boundary:]
                        if not payload or "Code=" not in payload:
                            continue
                        ev_code = ev_action = ""
                        ev_index = 0
                        for part in payload.split(";"):
                            part = part.strip()
                            if part.startswith("Code="):
                                ev_code = part[5:].strip()
                            elif part.startswith("action="):
                                ev_action = part[7:].strip()
                            elif part.startswith("index="):
                                try:
                                    ev_index = int(part[6:].strip())
                                except ValueError:
                                    pass
                        if not ev_code or not ev_action:
                            continue
                        channel_number = _parse_channel_number(index=ev_index)
                        event = {
                            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "code": ev_code,
                            "action": ev_action,
                            "index": ev_index,
                            "channel_number": channel_number,
                            "source": "nvr",
                            "path_name": f"ch{channel_number}" if channel_number else None,
                        }
                        # Event tetap dicatat semua; yang di-gate cuma medianya
                        # (lihat _claim_capture_slot).
                        capture_media = (
                            _should_capture_event_snapshot(ev_code, ev_action)
                            and _claim_capture_slot(channel_number, ev_code)
                        )
                        snapshot_path = None
                        if capture_media:
                            snapshot_path = _capture_nvr_event_snapshot(channel_number, ev_code)
                        event["id"] = _store_nvr_event(event, snapshot_path=snapshot_path)
                        if snapshot_path:
                            event["snapshot_url"] = f"/api/nvr-events/{event['id']}/snapshot"
                        if capture_media:
                            _start_event_clip_capture(event["id"], event.get("path_name"))
                        with _nvr_lock:
                            _nvr_events.appendleft(event)
                            _nvr_status["last_event"] = event["ts"]
                            _nvr_bump_locked()
        except Exception as e:
            with _nvr_lock:
                _nvr_status["connected"] = False
                _nvr_status["error"] = str(e)[:120]
                _nvr_bump_locked()
        time.sleep(5)


@app.route('/api/nvr-events', methods=['GET'])
def get_nvr_events():
    return jsonify(_nvr_payload_snapshot())


@app.route('/api/nvr-events/stream', methods=['GET'])
def stream_nvr_events():
    def generate():
        with _nvr_cond:
            last_revision = _nvr_revision
        initial_payload = json.dumps(_nvr_payload_snapshot())
        yield f"data: {initial_payload}\n\n"

        while True:
            with _nvr_cond:
                _nvr_cond.wait(timeout=25)
                if _nvr_revision == last_revision:
                    should_refresh = False
                else:
                    last_revision = _nvr_revision
                    should_refresh = True
            if not should_refresh:
                yield ": keepalive\n\n"
                continue
            payload = json.dumps(_nvr_payload_snapshot())
            yield f"data: {payload}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ── NVR Security Watch ───────────────────────────────────────────────────────
# Latar: pernah muncul akun admin misterius ("CISA") di NVR — indikasi akses
# tidak sah. Worker ini memantau dua hal lewat CGI API Dahua:
#   1. Daftar akun user NVR — akun baru/hilang memicu event NvrUserAdded/
#      NvrUserRemoved di feed event dashboard (baseline direkam diam-diam
#      pada scan pertama).
#   2. Log login NVR — login oleh user di luar akun service dashboard
#      (stream/event user) memicu event NvrLoginDetected.
# Semua event masuk ke tabel nvr_events (tampil di feed + tersinkron ke
# Postgres monitoring), jadi jejaknya bisa diaudit dari DBeaver juga.

NVR_SECURITY_SCAN_SECS = int(os.getenv("NVR_SECURITY_SCAN_SECS", "300"))


def _parse_dahua_indexed(text, prefix):
    """Parse baris 'prefix[N].Key=V' / 'prefix[N].Key.Sub=V' → {N: {key: v}}."""
    out = {}
    pattern = re.compile(rf'^{re.escape(prefix)}\[(\d+)\]\.(.+?)=(.*)$')
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        idx, key, value = int(m.group(1)), m.group(2), m.group(3)
        out.setdefault(idx, {})[key] = value
    return out


def _emit_security_event(code, detail):
    event = {
        "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code": code,
        "action": "Security",
        "index": 0,
        "channel_number": None,
        "source": "security",
        **detail,
    }
    event["id"] = _store_nvr_event(event)
    with _nvr_lock:
        _nvr_events.appendleft(event)
        _nvr_bump_locked()
    print(f"[SECURITY] {code}: {detail}", flush=True)


def _nvr_security_scan_users():
    text = _nvr_cgi_text('/cgi-bin/userManager.cgi?action=getUserInfoAll')
    parsed = _parse_dahua_indexed(text, 'users')
    current = {}
    for info in parsed.values():
        name = info.get('Name')
        if name:
            current[name] = {
                'group': info.get('Group', ''),
                'memo': info.get('Memo', ''),
            }
    if not current:
        return  # respons tidak terparse — jangan rusak baseline

    prev_raw = _db_setting('nvr_user_snapshot')
    if not prev_raw:
        _set_db_setting('nvr_user_snapshot', json.dumps(current))
        print(f"[SECURITY] Baseline akun NVR direkam: {sorted(current)}", flush=True)
        return

    prev = json.loads(prev_raw)
    for name in sorted(set(current) - set(prev)):
        _emit_security_event('NvrUserAdded', {
            'username': name,
            'group': current[name]['group'],
            'memo': current[name]['memo'],
        })
    for name in sorted(set(prev) - set(current)):
        _emit_security_event('NvrUserRemoved', {'username': name})
    if current != prev:
        _set_db_setting('nvr_user_snapshot', json.dumps(current))


def _nvr_security_scan_logins():
    # Akun service milik dashboard sendiri login ke NVR terus-menerus
    # (RTSP/event stream) — di-ignore supaya feed tidak banjir.
    stream_user, _ = _get_nvr_stream_creds()
    event_user, _ = _get_nvr_event_creds()
    ignore = {u for u in (stream_user, event_user) if u}

    last = _db_setting('nvr_seclog_last')
    if not last:
        last = (datetime.utcnow() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')

    start = urlencode({'condition.StartTime': last, 'condition.EndTime': now_str})
    text = _nvr_cgi_text(f'/cgi-bin/log.cgi?action=startFind&{start}&condition.Types[0]=Login')
    m = re.search(r'token=(\d+)', text)
    if not m:
        return
    token = m.group(1)
    try:
        found = _nvr_cgi_text(f'/cgi-bin/log.cgi?action=doFind&token={token}&count=100')
        items = _parse_dahua_indexed(found, 'items')
    finally:
        try:
            _nvr_cgi_text(f'/cgi-bin/log.cgi?action=stopFind&token={token}')
        except Exception:
            pass

    max_ts = last
    for info in items.values():
        ts = info.get('Time', '')
        user = info.get('User') or info.get('UserName') or ''
        addr = info.get('LogAddress') or info.get('Detail.Address') or info.get('Address') or ''
        if ts > max_ts:
            max_ts = ts
        if not user or user in ignore:
            continue
        _emit_security_event('NvrLoginDetected', {
            'username': user,
            'from_address': addr,
            'nvr_time': ts,
        })
    if max_ts != last:
        _set_db_setting('nvr_seclog_last', max_ts)


_nvr_security_last_error = [None]


def _nvr_security_worker():
    time.sleep(30)  # beri waktu backend & konfigurasi NVR siap
    while True:
        for step in (_nvr_security_scan_users, _nvr_security_scan_logins):
            try:
                step()
                _nvr_security_last_error[0] = None
            except Exception as e:
                msg = str(e)[:120]
                # Log hanya saat error berubah — hindari spam tiap 5 menit
                if msg != _nvr_security_last_error[0]:
                    print(f"[SECURITY] scan gagal ({step.__name__}): {msg}", flush=True)
                    _nvr_security_last_error[0] = msg
        time.sleep(NVR_SECURITY_SCAN_SECS)


@app.route('/api/nvr-config', methods=['GET'])
def get_nvr_config():
    stream_user, stream_pass = _get_nvr_stream_creds()
    event_user = _db_setting('nvr_event_user') or DVR_EVENT_USER
    event_pass = _db_setting('nvr_event_pass') or DVR_EVENT_PASS
    return jsonify({
        'host': _get_nvr_host(),
        'http_port': _get_nvr_http_port(),
        'stream_user': stream_user,
        'stream_pass': stream_pass,
        'event_user': event_user,
        'event_pass': event_pass,
        'stream_quality': _get_stream_quality(),
    })


@app.route('/api/nvr-info', methods=['GET'])
def get_nvr_info():
    try:
        return jsonify({'ok': True, 'info': _build_nvr_info_payload()})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)[:200]}), 502


@app.route('/api/nvr-config', methods=['POST'])
def set_nvr_config():
    body = request.get_json(silent=True) or {}
    updated = {}
    if 'host' in body:
        val = (body['host'] or '').strip()
        if not val:
            return jsonify({'error': 'host cannot be empty'}), 400
        _set_db_setting('nvr_host', val)
        updated['host'] = val
    if 'http_port' in body:
        try:
            port = int(body['http_port'])
        except (TypeError, ValueError):
            return jsonify({'error': 'http_port must be a number'}), 400
        if not (0 < port < 65536):
            return jsonify({'error': 'http_port out of range'}), 400
        _set_db_setting('nvr_http_port', str(port))
        updated['http_port'] = port
    if 'stream_user' in body:
        val = (body['stream_user'] or '').strip()
        if not val:
            return jsonify({'error': 'stream_user cannot be empty'}), 400
        _set_db_setting('nvr_stream_user', val)
        updated['stream_user'] = val
    if 'stream_pass' in body:
        val = (body['stream_pass'] or '').strip()
        if not val:
            return jsonify({'error': 'stream_pass cannot be empty'}), 400
        _set_db_setting('nvr_stream_pass', val)
        updated['stream_pass'] = val
    if 'event_user' in body:
        val = (body['event_user'] or '').strip()
        if not val:
            return jsonify({'error': 'event_user cannot be empty'}), 400
        _set_db_setting('nvr_event_user', val)
        updated['event_user'] = val
    if 'event_pass' in body:
        val = (body['event_pass'] or '').strip()
        if not val:
            return jsonify({'error': 'event_pass cannot be empty'}), 400
        _set_db_setting('nvr_event_pass', val)
        updated['event_pass'] = val
    if 'stream_quality' in body:
        val = (body['stream_quality'] or '').strip()
        if val not in STREAM_QUALITIES:
            return jsonify({'error': f'stream_quality must be one of {sorted(STREAM_QUALITIES)}'}), 400
        _set_db_setting('stream_quality', val)
        updated['stream_quality'] = val
    return jsonify({'ok': True, 'updated': list(updated.keys())})


# ── Zones ───────────────────────────────────────────────────────────────────

@app.route('/api/zones', methods=['GET'])
def get_zones():
    rows = get_db().execute(
        "SELECT id, camera_id, name, points_json, enabled FROM zones ORDER BY camera_id, id"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/zones', methods=['POST'])
def add_zone():
    body = request.get_json(silent=True) or {}
    camera_id = body.get('camera_id')
    name = (body.get('name') or '').strip()
    points = body.get('points')  # [[x_norm, y_norm], ...]

    if not camera_id or not name:
        return jsonify({'error': 'camera_id and name required'}), 400
    if not isinstance(points, list) or len(points) < 3:
        return jsonify({'error': 'points must be an array of at least 3 [x,y] pairs'}), 400

    db = get_db()
    cur = db.execute(
        "INSERT INTO zones (camera_id, name, points_json, enabled) VALUES (?,?,?,1)",
        (camera_id, name, json.dumps(points))
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'camera_id': camera_id, 'name': name,
                    'points_json': json.dumps(points), 'enabled': 1}), 201


@app.route('/api/zones/<int:zone_id>', methods=['DELETE'])
def delete_zone(zone_id):
    db = get_db()
    if not db.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone():
        return jsonify({'error': 'not found'}), 404
    db.execute("DELETE FROM zones WHERE id=?", (zone_id,))
    db.commit()
    return '', 204


@app.route('/api/zones/<int:zone_id>', methods=['PATCH'])
def update_zone(zone_id):
    db = get_db()
    if not db.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone():
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    fields, vals = [], []
    if 'enabled' in body:
        fields.append('enabled=?')
        vals.append(1 if body['enabled'] else 0)
    if 'name' in body:
        fields.append('name=?')
        vals.append(body['name'].strip())
    if fields:
        vals.append(zone_id)
        db.execute(f"UPDATE zones SET {', '.join(fields)} WHERE id=?", vals)
        db.commit()
    return jsonify(dict(db.execute("SELECT * FROM zones WHERE id=?", (zone_id,)).fetchone()))


# ── Known Faces ──────────────────────────────────────────────────────────────

@app.route('/api/faces', methods=['GET'])
def get_faces():
    include_photo = request.args.get('include_photo') == '1'
    rows = get_db().execute(
        "SELECT id, name, photo_path, created_at FROM known_faces ORDER BY id"
    ).fetchall()
    result = []
    for r in rows:
        d = {'id': r['id'], 'name': r['name'], 'created_at': r['created_at']}
        if include_photo and r['photo_path'] and os.path.exists(r['photo_path']):
            with open(r['photo_path'], 'rb') as f:
                d['photo_b64'] = base64.b64encode(f.read()).decode()
        result.append(d)
    return jsonify(result)


@app.route('/api/faces', methods=['POST'])
def add_face():
    name = (request.form.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name is required'}), 400
    photo = request.files.get('photo')
    if not photo:
        return jsonify({'error': 'photo is required'}), 400

    photo_data = photo.read()
    # Validate it's a readable image
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(photo_data))
        img.convert("RGB")  # forces full decode; raises on corrupt files
    except Exception:
        return jsonify({'error': 'invalid image file'}), 400

    os.makedirs(FACE_PHOTO_DIR, exist_ok=True)
    db = get_db()
    cur = db.execute(
        "INSERT INTO known_faces (name, photo_path, created_at) VALUES (?,?,?)",
        (name, None, datetime.utcnow().isoformat())
    )
    face_id = cur.lastrowid
    db.commit()

    photo_path = os.path.join(FACE_PHOTO_DIR, f"{face_id}.jpg")
    # Save as JPEG regardless of input format
    from PIL import Image
    img = Image.open(io.BytesIO(photo_data)).convert("RGB")
    img.save(photo_path, "JPEG", quality=90)

    db.execute("UPDATE known_faces SET photo_path=? WHERE id=?", (photo_path, face_id))
    db.commit()
    return jsonify({'id': face_id, 'name': name}), 201


@app.route('/api/faces/<int:face_id>', methods=['DELETE'])
def delete_face(face_id):
    db = get_db()
    row = db.execute("SELECT photo_path FROM known_faces WHERE id=?", (face_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    if row['photo_path'] and os.path.exists(row['photo_path']):
        os.remove(row['photo_path'])
    db.execute("DELETE FROM known_faces WHERE id=?", (face_id,))
    db.commit()
    return '', 204


@app.route('/api/faces/<int:face_id>/photo')
def get_face_photo(face_id):
    row = get_db().execute("SELECT photo_path FROM known_faces WHERE id=?", (face_id,)).fetchone()
    if not row or not row['photo_path'] or not os.path.exists(row['photo_path']):
        return jsonify({'error': 'photo not found'}), 404
    with open(row['photo_path'], 'rb') as f:
        data = f.read()
    return data, 200, {'Content-Type': 'image/jpeg', 'Cache-Control': 'no-cache'}


# ── Home / Away Mode ─────────────────────────────────────────────────────────

ALARM_EVENT_TYPES = {'ZoneIntrusion', 'UnknownFace', 'FaceRecognized'}


def _get_current_mode(db):
    row = db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
    return row['value'] if row else 'home'


def _trigger_alarm(event_body):
    """Called when mode=away and a human detection event arrives.
    Extend this function to send webhooks, trigger a siren, etc.
    """
    print(
        f"[ALARM] TRIGGERED – event={event_body.get('event_type')}"
        f", ch={event_body.get('channel_id')}"
        f", person={event_body.get('person_name')}"
        f", zone={event_body.get('zone_name')}",
        flush=True,
    )
    # TODO: send webhook / push notification / siren


@app.route('/api/mode', methods=['GET'])
def get_mode():
    mode = _get_current_mode(get_db())
    return jsonify({'mode': mode})


@app.route('/api/mode', methods=['POST'])
def update_mode():
    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'home')
    if mode not in ('home', 'away'):
        return jsonify({'error': 'mode must be \'home\' or \'away\''}), 400
    db = get_db()
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('mode', ?)", (mode,))
    db.commit()
    print(f"[MODE] Changed to '{mode}'", flush=True)
    return jsonify({'mode': mode})


# ── AI Global Toggle ─────────────────────────────────────────────────────────
# Master switch terpisah dari toggle ai_enabled per-kamera: kalau ini OFF,
# analyzer berhenti total (semua worker thread, apa pun status per-kamera) —
# dipakai untuk fokus ke streaming saja saat server sedang berat.

@app.route('/api/ai-config', methods=['GET'])
def get_ai_config():
    enabled = _db_setting('ai_global_enabled', '1') == '1'
    return jsonify({'enabled': enabled})


@app.route('/api/ai-config', methods=['POST'])
def update_ai_config():
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled', True))
    _set_db_setting('ai_global_enabled', '1' if enabled else '0')
    print(f"[AI] Global toggle changed to {'ON' if enabled else 'OFF'}", flush=True)
    return jsonify({'enabled': enabled})


# ── Analyzer Events (intake + read) ──────────────────────────────────────────

@app.route('/api/analyzer-event', methods=['POST'])
def receive_analyzer_event():
    body = request.get_json(silent=True) or {}
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    db = get_db()
    extra_payload = None
    extra_raw = body.get('extra_json')
    if isinstance(extra_raw, dict):
        extra_payload = extra_raw
    elif isinstance(extra_raw, str) and extra_raw.strip():
        try:
            parsed = json.loads(extra_raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            extra_payload = parsed

    # Check alarm condition: mode=away + human detection event
    current_mode = _get_current_mode(db)
    event_type   = body.get('event_type', 'Unknown')
    alarm        = 1 if (current_mode == 'away' and event_type in ALARM_EVENT_TYPES) else 0

    # Crop wajah dari frame asli saat deteksi (dikirim analyzer untuk
    # FaceRecognized/UnknownFace) — lihat _save_face_event_photo.
    face_snapshot_path = _save_face_event_photo(body.get('face_photo_b64'), event_type)

    db.execute(
        """INSERT INTO detection_events
           (ts, channel_id, camera_name, event_type, zone_name, person_name, confidence, extra_json, alarm_triggered, snapshot_path)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            ts,
            body.get('channel_id', ''),
            body.get('camera_name', ''),
            event_type,
            body.get('zone_name'),
            body.get('person_name'),
            body.get('confidence'),
            body.get('extra_json'),
            alarm,
            face_snapshot_path,
        )
    )
    db.commit()

    if alarm:
        _trigger_alarm(body)

    # Also push into in-memory NVR event deque so NVREventLog picks it up live
    event = {
        'ts':             ts,
        'code':           event_type,
        'action':         'Start',
        'index':          0,
        'channel_number': _parse_channel_number(channel_value=body.get('channel_id')),
        'source':         'analyzer',
        'path_name':      body.get('channel_id', ''),
        'zone_name':      body.get('zone_name'),
        'person':         body.get('person_name'),
        'channel':        body.get('channel_id', ''),
        'alarm':          bool(alarm),
    }
    if extra_payload:
        event.update(extra_payload)
    if event['channel_number']:
        event['index'] = max(event['channel_number'] - 1, 0)
    # Kalau analyzer sudah kirim crop wajah, pakai itu — hindari re-fetch
    # snapshot channel penuh ke NVR yang lebih lambat dan kurang presisi.
    snapshot_path = face_snapshot_path
    if not snapshot_path and _should_capture_event_snapshot(event_type, 'Start'):
        snapshot_path = _capture_nvr_event_snapshot(event['channel_number'], event_type)
    event['id'] = _store_nvr_event(event, snapshot_path=snapshot_path)
    if snapshot_path:
        event['snapshot_url'] = f"/api/nvr-events/{event['id']}/snapshot"
    if _should_capture_event_snapshot(event_type, 'Start'):
        _start_event_clip_capture(event['id'], event.get('path_name'))
    with _nvr_lock:
        _nvr_events.appendleft(event)
        _nvr_status['last_event'] = ts
        _nvr_bump_locked()
    return jsonify({'ok': True, 'alarm_triggered': bool(alarm)})


@app.route('/api/detection-events', methods=['GET'])
def get_detection_events():
    limit  = min(int(request.args.get('limit',  50)), 200)
    offset = int(request.args.get('offset', 0))
    rows = get_db().execute(
        """SELECT id, ts, channel_id, camera_name, event_type, zone_name,
                  person_name, confidence, snapshot_path
           FROM detection_events ORDER BY id DESC LIMIT ? OFFSET ?""",
        (limit, offset)
    ).fetchall()
    total = get_db().execute("SELECT COUNT(*) FROM detection_events").fetchone()[0]
    events = []
    for row in rows:
        ev = dict(row)
        if ev.pop('snapshot_path', None):
            ev['snapshot_url'] = f"/api/detection-events/{ev['id']}/snapshot"
        events.append(ev)
    return jsonify({'total': total, 'events': events})


@app.route('/api/detection-events/<int:event_id>/snapshot', methods=['GET'])
def get_detection_event_snapshot(event_id):
    row = get_db().execute("SELECT snapshot_path FROM detection_events WHERE id=?", (event_id,)).fetchone()
    if not row or not row['snapshot_path'] or not os.path.exists(row['snapshot_path']):
        return jsonify({'error': 'snapshot not found'}), 404
    with open(row['snapshot_path'], 'rb') as fh:
        data = fh.read()
    return data, 200, {'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=300'}


@app.route('/api/detection-events', methods=['DELETE'])
def clear_detection_events():
    get_db().execute("DELETE FROM detection_events")
    get_db().commit()
    return jsonify({'ok': True})


# ── Server Resource Stats ────────────────────────────────────────────────────

DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")


def _docker_api_get(path, timeout=6):
    """GET ke Docker Engine API lewat unix socket, tanpa dependency tambahan."""
    import http.client
    import socket as _socket

    class _UnixHTTPConnection(http.client.HTTPConnection):
        def __init__(self):
            super().__init__("localhost", timeout=timeout)

        def connect(self):
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(DOCKER_SOCK)
            self.sock = sock

    conn = _UnixHTTPConnection()
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        return json.loads(resp.read().decode())
    finally:
        conn.close()


def _read_cpu_totals():
    with open("/proc/stat") as fh:
        parts = fh.readline().split()[1:]
    values = [int(v) for v in parts]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _host_stats():
    total1, idle1 = _read_cpu_totals()
    time.sleep(0.25)
    total2, idle2 = _read_cpu_totals()
    dt, di = total2 - total1, idle2 - idle1
    cpu_percent = round((1 - di / dt) * 100, 1) if dt > 0 else None

    meminfo = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            meminfo[key.strip()] = int(rest.strip().split()[0]) * 1024  # kB → bytes
    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)

    disk = shutil.disk_usage("/data")

    return {
        "cpu_percent": cpu_percent,
        "cpu_count": os.cpu_count(),
        "load_avg": list(os.getloadavg()),
        "mem_total": mem_total,
        "mem_used": mem_total - mem_available,
        "disk_total": disk.total,
        "disk_used": disk.used,
    }


def _container_stats(container):
    cid = container.get("Id")
    name = (container.get("Names") or ["?"])[0].lstrip("/")
    entry = {
        "name": name,
        "image": container.get("Image"),
        "state": container.get("State"),
        "status": container.get("Status"),
        "cpu_percent": None,
        "mem_used": None,
        "mem_limit": None,
    }
    if container.get("State") != "running":
        return entry
    try:
        # Path tanpa prefix versi → Docker pakai versi API terbarunya sendiri
        stats = _docker_api_get(f"/containers/{cid}/stats?stream=false")
        if not stats:
            return entry
        cpu = stats.get("cpu_stats") or {}
        precpu = stats.get("precpu_stats") or {}
        cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - \
                    (precpu.get("cpu_usage") or {}).get("total_usage", 0)
        sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
        online_cpus = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
        if cpu_delta > 0 and sys_delta > 0:
            entry["cpu_percent"] = round((cpu_delta / sys_delta) * online_cpus * 100, 1)
        mem = stats.get("memory_stats") or {}
        usage = mem.get("usage")
        if usage is not None:
            # Kurangi page cache supaya sesuai dengan angka `docker stats`
            cache = (mem.get("stats") or {}).get("inactive_file", 0)
            entry["mem_used"] = max(usage - cache, 0)
            entry["mem_limit"] = mem.get("limit")
    except Exception:
        pass
    return entry


@app.route('/api/server-stats', methods=['GET'])
def get_server_stats():
    payload = {"ok": True, "host": None, "containers": [], "docker_available": False}
    try:
        payload["host"] = _host_stats()
    except Exception as exc:
        payload["host_error"] = str(exc)[:120]

    if os.path.exists(DOCKER_SOCK):
        try:
            containers = _docker_api_get("/containers/json?all=true") or []
            payload["docker_available"] = True
            with ThreadPoolExecutor(max_workers=6) as ex:
                payload["containers"] = sorted(
                    ex.map(_container_stats, containers),
                    key=lambda c: (c["state"] != "running", c["name"]),
                )
        except Exception as exc:
            payload["docker_error"] = str(exc)[:120]

    return jsonify(payload)


# ── Camera Snapshot ──────────────────────────────────────────────────────────

@app.route('/api/cameras/<int:cam_id>/snapshot')
def camera_snapshot(cam_id):
    row = get_db().execute("SELECT stream_url FROM cameras WHERE id=?", (cam_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404
    from urllib.parse import urlsplit
    path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]
    snap_path = os.path.join(SNAPSHOT_DIR, f"{path_seg}.jpg")
    if not os.path.exists(snap_path):
        return jsonify({'error': 'snapshot not yet available; start the analyzer service'}), 404
    with open(snap_path, 'rb') as f:
        data = f.read()
    return data, 200, {'Content-Type': 'image/jpeg', 'Cache-Control': 'no-cache'}


@app.route('/api/nvr-events/<int:event_id>/snapshot', methods=['GET'])
def get_nvr_event_snapshot(event_id):
    row = get_db().execute("SELECT snapshot_path FROM nvr_events WHERE id=?", (event_id,)).fetchone()
    if not row or not row['snapshot_path'] or not os.path.exists(row['snapshot_path']):
        return jsonify({'error': 'snapshot not found'}), 404
    with open(row['snapshot_path'], 'rb') as fh:
        data = fh.read()
    return data, 200, {'Content-Type': 'image/jpeg', 'Cache-Control': 'public, max-age=300'}


@app.route('/api/nvr-events/<int:event_id>/clip', methods=['GET'])
def get_nvr_event_clip(event_id):
    row = get_db().execute("SELECT clip_path FROM nvr_events WHERE id=?", (event_id,)).fetchone()
    if not row or not row['clip_path'] or not os.path.exists(row['clip_path']):
        return jsonify({'error': 'clip not found'}), 404
    return send_file(row['clip_path'], mimetype='video/mp4', conditional=True, max_age=300)


@app.route('/api/storage', methods=['GET'])
def get_storage_usage():
    """Rincian pemakaian disk media event + status janitor terakhir.
    Dipakai untuk memverifikasi budget MEDIA_MAX_GB benar-benar ditegakkan."""
    dirs = {}
    for directory in _MEDIA_DIRS:
        size = count = 0
        try:
            entries = os.scandir(directory)
        except (FileNotFoundError, NotADirectoryError):
            entries = None
        if entries is not None:
            with entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            size += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        pass
        dirs[os.path.basename(directory)] = {'bytes': size, 'files': count}

    try:
        db_bytes = os.path.getsize(DB_PATH)
    except OSError:
        db_bytes = 0
    try:
        disk = shutil.disk_usage(_DATA_DIR)
        disk_info = {'total': disk.total, 'used': disk.used, 'free': disk.free}
    except OSError:
        disk_info = None

    managed = sum(d['bytes'] for d in dirs.values())
    return jsonify({
        'dirs': dirs,
        'managed_bytes': managed,
        'db_bytes': db_bytes,
        'budget_bytes': int(MEDIA_MAX_GB * 1024 ** 3) if MEDIA_MAX_GB > 0 else 0,
        'budget_gb': MEDIA_MAX_GB,
        'retention_days': MEDIA_RETENTION_DAYS,
        'clip_secs': NVR_EVENT_CLIP_SECS,
        'noisy_cooldown_secs': NVR_EVENT_NOISY_COOLDOWN_SECS,
        'event_db_max_rows': EVENT_DB_MAX_ROWS,
        'disk': disk_info,
        'janitor': dict(_media_usage),
    })


# ── Postgres monitoring mirror ───────────────────────────────────────────────
# DB terpisah untuk analisa/audit dari luar (DBeaver): backend menyalin tabel
# monitoring dari SQLite ke Postgres secara periodik (satu arah, incremental).
# SENGAJA tidak menyalin tabel users/settings — di sana ada password hash dan
# kredensial NVR; kolom rtsp_url kamera juga di-redact sebelum disalin.
# PG_HOST kosong = fitur mati total (aman kalau container pg tidak jalan).

PG_HOST = os.getenv("PG_HOST", "")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB   = os.getenv("PG_DB", "monitoring")
PG_USER = os.getenv("PG_USER", "monitor")
PG_PASS = os.getenv("PG_PASS", "")
PG_SYNC_SECS = int(os.getenv("PG_SYNC_SECS", "60"))

_PG_TABLES = {
    'login_log': "id BIGINT PRIMARY KEY, ts TEXT, username TEXT, success INT, ip TEXT, blocked INT",
    'detection_events': ("id BIGINT PRIMARY KEY, ts TEXT, channel_id TEXT, camera_name TEXT, "
                         "event_type TEXT, zone_name TEXT, person_name TEXT, confidence REAL, "
                         "alarm_triggered INT, extra_json TEXT"),
    'nvr_events': ("id BIGINT PRIMARY KEY, ts TEXT, code TEXT, action TEXT, event_index INT, "
                   "channel_number INT, source TEXT, extra_json TEXT"),
}
_PG_COLS = {name: [c.split()[0] for c in ddl.split(', ')] for name, ddl in _PG_TABLES.items()}


def _redact_url_creds(url):
    # [^/]* greedy sampai '@' TERAKHIR sebelum path — password yang mengandung
    # '@' (umum di kredensial NVR di sini) tetap ter-redact utuh.
    return re.sub(r'//[^/]*@', '//', url or '')


def _pg_sync_cycle():
    import psycopg2  # lazy: hanya dibutuhkan bila PG_HOST diisi
    pg = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                          user=PG_USER, password=PG_PASS, connect_timeout=5)
    pg.autocommit = True
    lite = sqlite3.connect(DB_PATH)
    lite.row_factory = sqlite3.Row
    try:
        cur = pg.cursor()
        for name, ddl in _PG_TABLES.items():
            cur.execute(f"CREATE TABLE IF NOT EXISTS {name} ({ddl})")
        cur.execute("""CREATE TABLE IF NOT EXISTS cameras (
            id BIGINT PRIMARY KEY, name TEXT, channel INT, builtin INT,
            ai_enabled INT, ptz_supported INT, stream_url TEXT, rtsp_url_redacted TEXT)""")

        for name, cols in _PG_COLS.items():
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {name}")
            last_id = cur.fetchone()[0]
            rows = lite.execute(
                f"SELECT {', '.join(cols)} FROM {name} WHERE id > ? ORDER BY id LIMIT 1000",
                (last_id,)
            ).fetchall()
            if rows:
                placeholders = ', '.join(['%s'] * len(cols))
                cur.executemany(
                    f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT (id) DO NOTHING",
                    [tuple(r[c] for c in cols) for r in rows],
                )

        # cameras: kecil — full refresh supaya rename/hapus ikut tercermin
        cams = lite.execute(
            "SELECT id, name, channel, builtin, ai_enabled, ptz_supported, stream_url, rtsp_url FROM cameras"
        ).fetchall()
        cur.execute("DELETE FROM cameras")
        cur.executemany(
            "INSERT INTO cameras VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(r['id'], r['name'], r['channel'], r['builtin'], r['ai_enabled'],
              r['ptz_supported'], r['stream_url'], _redact_url_creds(r['rtsp_url'])) for r in cams],
        )
    finally:
        lite.close()
        pg.close()


_pg_last_error = [None]


def _pg_sync_worker():
    time.sleep(15)
    while True:
        try:
            _pg_sync_cycle()
            if _pg_last_error[0] is not None:
                print("[PG-SYNC] pulih — sinkronisasi jalan lagi", flush=True)
            _pg_last_error[0] = None
        except Exception as e:
            msg = str(e)[:150]
            if msg != _pg_last_error[0]:
                print(f"[PG-SYNC] gagal: {msg}", flush=True)
                _pg_last_error[0] = msg
        time.sleep(PG_SYNC_SECS)


_nvr_thread = threading.Thread(target=_nvr_event_worker, daemon=True, name="nvr-events")
_nvr_thread.start()

threading.Thread(target=_nvr_security_worker, daemon=True, name="nvr-security").start()

if PG_HOST:
    threading.Thread(target=_pg_sync_worker, daemon=True, name="pg-sync").start()

threading.Thread(target=_media_janitor_worker, daemon=True, name="media-janitor").start()

init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
