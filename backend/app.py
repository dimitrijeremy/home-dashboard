import atexit
import base64
import collections
import hashlib
import io
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
import requests
from requests.auth import HTTPDigestAuth
from flask import Flask, Response, jsonify, request, g, stream_with_context
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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
CUSTOM_STREAM_PROCS = {}


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
DVR_HOST      = os.getenv("DVR_HOST", "10.10.30.2")
DVR_HTTP_PORT = int(os.getenv("DVR_HTTP_PORT", "80"))
DVR_USER      = os.getenv("DVR_USER", "dashboard")
DVR_PASS      = os.getenv("DVR_PASS", "d4$hb0ard-dlt")
# Separate credentials for the event stream (needs operator/admin on Dahua).
# Falls back to DVR_USER/DVR_PASS if not configured.
DVR_EVENT_USER = os.getenv("DVR_EVENT_USER") or DVR_USER
DVR_EVENT_PASS = os.getenv("DVR_EVENT_PASS") or DVR_PASS

# ── Camera Siren/Audio Config (DH-P5AE-PV or similar with speaker) ──────────
SIREN_CAMERA_HOST = os.getenv("SIREN_CAMERA_HOST", "")  # IP of camera with speaker
SIREN_CAMERA_USER = os.getenv("SIREN_CAMERA_USER", "") or DVR_USER
SIREN_CAMERA_PASS = os.getenv("SIREN_CAMERA_PASS", "") or DVR_PASS
SIREN_ENABLED     = os.getenv("SIREN_ENABLED", "true").lower() in ("1", "true", "yes")

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


def ensure_detection_columns(con):
    existing = {row[1] for row in con.execute("PRAGMA table_info(detection_events)")}
    if "alarm_triggered" not in existing:
        con.execute("ALTER TABLE detection_events ADD COLUMN alarm_triggered INTEGER NOT NULL DEFAULT 0")


def build_rtsp_source(rtsp_url, channel):
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
    pairs.insert(0, ("channel", channel_str))

    return urlunsplit(parts._replace(query=urlencode(pairs)))


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
    source_url = build_rtsp_source(rtsp_url, channel)
    log_handle = open(f"/tmp/{path_name}.log", "ab")
    proc = subprocess.Popen(
        [CUSTOM_STREAM_SCRIPT, source_url, path_name],
        cwd=APP_DIR,
        stdout=log_handle,
        stderr=log_handle,
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


def sync_camera_rows(con):
    builtin_rows = con.execute(
        "SELECT id FROM cameras WHERE builtin=1 ORDER BY sort_order, id"
    ).fetchall()

    for channel in range(1, 5):
        stream_url = _public_stream_url(f"ch{channel}")
        name = f"Camera {channel}"
        if channel <= len(builtin_rows):
            con.execute(
                "UPDATE cameras SET name=?, stream_url=?, sort_order=? WHERE id=?",
                (name, stream_url, channel, builtin_rows[channel - 1][0]),
            )
        else:
            con.execute(
                "INSERT INTO cameras (name, stream_url, sort_order, builtin, rtsp_url, channel) VALUES (?,?,?,?,?,?)",
                (name, stream_url, channel, 1, None, None),
            )

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
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Seed default settings
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('mode', 'home')")
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_stream_user', ?)" , (DVR_USER,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_stream_pass', ?)" , (DVR_PASS,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_event_user', ?)" , (DVR_EVENT_USER,))
    con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('nvr_event_pass', ?)" , (DVR_EVENT_PASS,))
    ensure_detection_columns(con)
    sync_camera_rows(con)
    con.commit()
    restore_custom_streams(con)
    con.close()

# ── Routes ──────────────────────────────────────────────────

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    rows = get_db().execute(
        "SELECT id, name, stream_url, builtin FROM cameras ORDER BY sort_order, id"
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

        max_order = db.execute("SELECT COALESCE(MAX(sort_order),0) FROM cameras").fetchone()[0]
        cur = db.execute(
            "INSERT INTO cameras (name, stream_url, sort_order, builtin, rtsp_url, channel) VALUES (?,?,?,0,?,?)",
            (name, stream_url, max_order + 1, custom_rtsp_url, custom_channel)
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

    payload = {'id': camera_id, 'name': name, 'stream_url': stream_url, 'builtin': 0}
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
    row = db.execute("SELECT id FROM cameras WHERE id=?", (cam_id,)).fetchone()
    if not row:
        return jsonify({'error': 'not found'}), 404

    body = request.get_json(silent=True) or {}
    fields, vals = [], []
    if 'name' in body:
        name = body['name'].strip()
        if not name:
            return jsonify({'error': 'name cannot be empty'}), 400
        fields.append('name=?'); vals.append(name)
    if 'stream_url' in body:
        url = body['stream_url'].strip()
        if not url.startswith('http'):
            return jsonify({'error': 'stream_url must start with http'}), 400
        fields.append('stream_url=?'); vals.append(url)

    if fields:
        vals.append(cam_id)
        db.execute(f"UPDATE cameras SET {', '.join(fields)} WHERE id=?", vals)
        db.commit()

    updated = db.execute("SELECT id, name, stream_url, builtin FROM cameras WHERE id=?", (cam_id,)).fetchone()
    return jsonify(dict(updated))


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
        # Kill ffmpeg pushing to this path — mediamtx runOnInitRestart respawns it
        path_seg = urlsplit(row['stream_url']).path.strip('/').split('/')[0]  # e.g. "ch1"
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
            _restart_host_rtsp_publisher(path_seg)
        else:
            path_name = extract_custom_path_name(row['stream_url'])
            if path_name and row['rtsp_url'] and row['channel']:
                stop_custom_stream(path_name)
                launch_custom_stream(path_name, row['rtsp_url'], row['channel'])
    return jsonify({'ok': True})


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
    if not DVR_HOST:
        raise RuntimeError('NVR host is not configured')

    last_error = 'NVR request failed'
    url = f"http://{DVR_HOST}:{DVR_HTTP_PORT}{path}"
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
        'host': DVR_HOST,
        'http_port': DVR_HTTP_PORT,
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
    if not DVR_HOST:
        return
    # Initial startup delay: wait before first attempt to avoid hammering NVR
    # immediately on container start (which can trigger account lockout).
    time.sleep(60)
    url = f"http://{DVR_HOST}:{DVR_HTTP_PORT}/cgi-bin/eventManager.cgi?action=attach&codes=[All]&heartbeat=5"
    while True:
        # Re-read credentials from DB each reconnect so config changes take effect
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
                        event = {
                            "ts": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "code": ev_code,
                            "action": ev_action,
                            "index": ev_index,
                        }
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
    with _nvr_lock:
        return jsonify(_nvr_snapshot_locked())


@app.route('/api/nvr-events/stream', methods=['GET'])
def stream_nvr_events():
    def generate():
        with _nvr_cond:
            last_revision = _nvr_revision
            initial_payload = json.dumps(_nvr_snapshot_locked())
        yield f"data: {initial_payload}\n\n"

        while True:
            with _nvr_cond:
                _nvr_cond.wait(timeout=25)
                if _nvr_revision == last_revision:
                    payload = None
                else:
                    last_revision = _nvr_revision
                    payload = json.dumps(_nvr_snapshot_locked())
            if payload is None:
                yield ": keepalive\n\n"
                continue
            yield f"data: {payload}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@app.route('/api/nvr-config', methods=['GET'])
def get_nvr_config():
    stream_user, stream_pass = _get_nvr_stream_creds()
    event_user = _db_setting('nvr_event_user') or DVR_EVENT_USER
    event_pass = _db_setting('nvr_event_pass') or DVR_EVENT_PASS
    return jsonify({
        'host': DVR_HOST,
        'http_port': DVR_HTTP_PORT,
        'stream_user': stream_user,
        'stream_pass': stream_pass,
        'event_user': event_user,
        'event_pass': event_pass,
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


def _get_siren_config():
    """Get current siren configuration from DB with env fallback."""
    host = _db_setting('siren_host', SIREN_CAMERA_HOST)
    user = _db_setting('siren_user', SIREN_CAMERA_USER) or DVR_USER
    passwd = _db_setting('siren_pass', SIREN_CAMERA_PASS) or DVR_PASS
    enabled = _db_setting('siren_enabled', 'true' if SIREN_ENABLED else 'false') == 'true'
    return host, user, passwd, enabled


def _trigger_siren(channel: int = 1):
    """Trigger siren/speaker on camera (DH-P5AE-PV or similar Dahua with built-in speaker).
    Uses coaxialControl CGI for alarm output and audioOutput for tone playback.
    """
    host, user, passwd, enabled = _get_siren_config()
    if not host or not enabled:
        print("[SIREN] Skipped — SIREN_CAMERA_HOST not configured or disabled", flush=True)
        return False

    endpoints = [
        # Primary: coaxial alarm (works on most Dahua cameras with speaker)
        f"/cgi-bin/coaxialControl.cgi?action=control&channel={channel}&info[0].Type=Speaker",
        # Fallback: direct alarm trigger
        f"/cgi-bin/alarm.cgi?action=start&channel={channel}",
    ]

    for path in endpoints:
        try:
            resp = requests.get(
                f"http://{host}{path}",
                auth=HTTPDigestAuth(user, passwd),
                timeout=5,
            )
            if resp.status_code == 200:
                print(f"[SIREN] Triggered OK via {path}", flush=True)
                return True
            print(f"[SIREN] {path} → HTTP {resp.status_code}", flush=True)
        except Exception as e:
            print(f"[SIREN] {path} error: {e}", flush=True)

    return False


def _stop_siren(channel: int = 1):
    """Stop siren/speaker on camera."""
    host, user, passwd, enabled = _get_siren_config()
    if not host:
        return False
    try:
        resp = requests.get(
            f"http://{host}/cgi-bin/alarm.cgi?action=stop&channel={channel}",
            auth=HTTPDigestAuth(user, passwd),
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[SIREN] stop error: {e}", flush=True)
        return False


def _nvr_guard_set(armed: bool) -> dict:
    """Set NVR guard mode (arm/disarm).
    Tries multiple CGI endpoints since Dahua firmware varies.
    """
    user, passwd = _get_nvr_event_creds()
    mode_str = "Start" if armed else "Stop"

    endpoints = [
        # Primary: configManager approach
        f"/cgi-bin/configManager.cgi?action=setConfig&Alarm_ARM={mode_str}",
        # Alternate: SecurityManager
        f"/cgi-bin/SecurityManager.cgi?action={'arm' if armed else 'disarm'}",
    ]

    for path in endpoints:
        try:
            resp = requests.get(
                f"http://{DVR_HOST}:{DVR_HTTP_PORT}{path}",
                auth=HTTPDigestAuth(user, passwd),
                timeout=8,
            )
            if resp.status_code == 200 and "OK" in resp.text:
                print(f"[NVR-GUARD] {'Armed' if armed else 'Disarmed'} OK via {path}", flush=True)
                return {"ok": True, "armed": armed}
            print(f"[NVR-GUARD] {path} → HTTP {resp.status_code}: {resp.text[:100]}", flush=True)
        except Exception as e:
            print(f"[NVR-GUARD] {path} error: {e}", flush=True)

    return {"ok": False, "error": "All endpoints failed", "armed": None}


def _nvr_guard_get() -> dict:
    """Get current NVR guard/arm status."""
    user, passwd = _get_nvr_event_creds()
    try:
        resp = requests.get(
            f"http://{DVR_HOST}:{DVR_HTTP_PORT}/cgi-bin/configManager.cgi?action=getConfig&name=Alarm_ARM",
            auth=HTTPDigestAuth(user, passwd),
            timeout=8,
        )
        if resp.status_code == 200:
            body = resp.text.strip()
            armed = "Start" in body or "true" in body.lower()
            return {"ok": True, "armed": armed, "raw": body[:200]}
        return {"ok": False, "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        print(f"[NVR-GUARD] get error: {e}", flush=True)
        return {"ok": False, "error": "NVR connection failed"}


def _trigger_alarm(event_body):
    """Called when alarm condition is met.
    Triggers siren on camera speaker + logs alarm.
    """
    print(
        f"[ALARM] TRIGGERED – event={event_body.get('event_type')}"
        f", ch={event_body.get('channel_id')}"
        f", person={event_body.get('person_name')}"
        f", zone={event_body.get('zone_name')}",
        flush=True,
    )
    # Trigger camera siren in background thread to not block request
    threading.Thread(target=_trigger_siren, daemon=True).start()


def _check_zone_alarm(event_body, current_mode, db):
    """Check per-zone alarm settings to determine if alarm or chime should trigger.
    Returns: 'alarm', 'chime', or None
    """
    zone_name = event_body.get('zone_name')
    if not zone_name:
        # Fallback to global logic
        event_type = event_body.get('event_type', 'Unknown')
        if current_mode == 'away' and event_type in ALARM_EVENT_TYPES:
            return 'alarm'
        return None

    # Find zone by name
    row = db.execute("SELECT id FROM zones WHERE name=?", (zone_name,)).fetchone()
    if not row:
        # Zone not found, fallback
        event_type = event_body.get('event_type', 'Unknown')
        if current_mode == 'away' and event_type in ALARM_EVENT_TYPES:
            return 'alarm'
        return None

    zone_id = row['id']
    settings_raw = _db_setting(f'zone_{zone_id}_alarm', '')
    if settings_raw:
        try:
            settings = json.loads(settings_raw)
        except Exception:
            settings = {}
    else:
        settings = {}

    trigger_on_home = settings.get('trigger_on_home', False)
    trigger_on_away = settings.get('trigger_on_away', True)
    chime_on_home = settings.get('chime_on_home', True)

    event_type = event_body.get('event_type', 'Unknown')
    if event_type not in ALARM_EVENT_TYPES:
        return None

    if current_mode == 'away' and trigger_on_away:
        return 'alarm'
    if current_mode == 'home' and trigger_on_home:
        return 'alarm'
    if current_mode == 'home' and chime_on_home:
        return 'chime'
    return None


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

    # Sync NVR guard mode with home/away in background
    def _sync_nvr():
        result = _nvr_guard_set(armed=(mode == 'away'))
        if not result.get('ok'):
            print(f"[MODE] NVR guard sync failed: {result.get('error')}", flush=True)
    threading.Thread(target=_sync_nvr, daemon=True).start()

    return jsonify({'mode': mode})


# ── NVR Guard (Arm/Disarm) ────────────────────────────────────────────────────

@app.route('/api/nvr-guard', methods=['GET'])
def get_nvr_guard():
    """Get current NVR armed/disarmed status."""
    result = _nvr_guard_get()
    return jsonify(result)


@app.route('/api/nvr-guard', methods=['POST'])
def set_nvr_guard():
    """Set NVR armed/disarmed status. Body: {"armed": true/false}"""
    body = request.get_json(silent=True) or {}
    armed = body.get('armed', True)
    result = _nvr_guard_set(armed=armed)
    status_code = 200 if result.get('ok') else 502
    return jsonify(result), status_code


# ── Siren/Speaker Control ─────────────────────────────────────────────────────

@app.route('/api/siren', methods=['POST'])
def trigger_siren_endpoint():
    """Trigger siren on camera speaker. Body: {"channel": 1}"""
    body = request.get_json(silent=True) or {}
    channel = body.get('channel', 1)
    ok = _trigger_siren(channel=channel)
    return jsonify({'ok': ok})


@app.route('/api/siren/stop', methods=['POST'])
def stop_siren_endpoint():
    """Stop siren on camera speaker."""
    body = request.get_json(silent=True) or {}
    channel = body.get('channel', 1)
    ok = _stop_siren(channel=channel)
    return jsonify({'ok': ok})


# ── Analyzer Events (intake + read) ──────────────────────────────────────────

@app.route('/api/analyzer-event', methods=['POST'])
def receive_analyzer_event():
    body = request.get_json(silent=True) or {}
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    db = get_db()

    # Check alarm condition using per-zone settings
    current_mode = _get_current_mode(db)
    event_type   = body.get('event_type', 'Unknown')

    # Per-zone alarm logic
    zone_result = _check_zone_alarm(body, current_mode, db)
    alarm = 1 if zone_result == 'alarm' else 0

    db.execute(
        """INSERT INTO detection_events
           (ts, channel_id, camera_name, event_type, zone_name, person_name, confidence, extra_json, alarm_triggered)
           VALUES (?,?,?,?,?,?,?,?,?)""",
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
        )
    )
    db.commit()

    if zone_result == 'alarm':
        _trigger_alarm(body)
    elif zone_result == 'chime':
        print(f"[CHIME] Zone '{body.get('zone_name')}' triggered chime (home mode)", flush=True)
        # Chime uses a softer trigger (same endpoint, but logged differently)

    # Also push into in-memory NVR event deque so NVREventLog picks it up live
    event = {
        'ts':             ts,
        'code':           event_type,
        'action':         'Start',
        'index':          0,
        'zone_name':      body.get('zone_name'),
        'person':         body.get('person_name'),
        'channel':        body.get('channel_id', ''),
        'alarm':          bool(alarm),
        'chime':          zone_result == 'chime',
    }
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
                  person_name, confidence
           FROM detection_events ORDER BY id DESC LIMIT ? OFFSET ?""",
        (limit, offset)
    ).fetchall()
    total = get_db().execute("SELECT COUNT(*) FROM detection_events").fetchone()[0]
    return jsonify({'total': total, 'events': [dict(r) for r in rows]})


@app.route('/api/detection-events', methods=['DELETE'])
def clear_detection_events():
    get_db().execute("DELETE FROM detection_events")
    get_db().commit()
    return jsonify({'ok': True})


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


# ── Siren Config CRUD ─────────────────────────────────────────────────────────

@app.route('/api/siren-config', methods=['GET'])
def get_siren_config():
    """Get siren configuration from DB settings."""
    return jsonify({
        'host': _db_setting('siren_host', SIREN_CAMERA_HOST),
        'user': _db_setting('siren_user', SIREN_CAMERA_USER),
        'pass': _db_setting('siren_pass', SIREN_CAMERA_PASS),
        'enabled': _db_setting('siren_enabled', 'true' if SIREN_ENABLED else 'false') == 'true',
    })


@app.route('/api/siren-config', methods=['POST'])
def set_siren_config():
    """Save siren configuration to DB settings."""
    body = request.get_json(silent=True) or {}
    if 'host' in body:
        _set_db_setting('siren_host', (body['host'] or '').strip())
    if 'user' in body:
        _set_db_setting('siren_user', (body['user'] or '').strip())
    if 'pass' in body:
        _set_db_setting('siren_pass', body['pass'])
    if 'enabled' in body:
        _set_db_setting('siren_enabled', 'true' if body['enabled'] else 'false')

    # Update runtime globals
    global SIREN_CAMERA_HOST, SIREN_CAMERA_USER, SIREN_CAMERA_PASS, SIREN_ENABLED
    SIREN_CAMERA_HOST = _db_setting('siren_host', '')
    SIREN_CAMERA_USER = _db_setting('siren_user', '') or DVR_USER
    SIREN_CAMERA_PASS = _db_setting('siren_pass', '') or DVR_PASS
    SIREN_ENABLED = _db_setting('siren_enabled', 'true') == 'true'

    return jsonify({'ok': True})


# ── Zone Alarm Settings ───────────────────────────────────────────────────────

@app.route('/api/zones/<int:zone_id>/alarm-settings', methods=['GET'])
def get_zone_alarm_settings(zone_id):
    """Get alarm settings for a zone."""
    db = get_db()
    if not db.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone():
        return jsonify({'error': 'not found'}), 404
    settings_raw = _db_setting(f'zone_{zone_id}_alarm', '')
    if settings_raw:
        try:
            return jsonify(json.loads(settings_raw))
        except Exception:
            pass
    # Default settings
    return jsonify({
        'trigger_on_home': False,
        'trigger_on_away': True,
        'sound_file': 'alarm',
        'chime_on_home': True,
    })


@app.route('/api/zones/<int:zone_id>/alarm-settings', methods=['POST'])
def set_zone_alarm_settings(zone_id):
    """Set alarm settings for a zone."""
    db = get_db()
    if not db.execute("SELECT id FROM zones WHERE id=?", (zone_id,)).fetchone():
        return jsonify({'error': 'not found'}), 404
    body = request.get_json(silent=True) or {}
    settings = {
        'trigger_on_home': bool(body.get('trigger_on_home', False)),
        'trigger_on_away': bool(body.get('trigger_on_away', True)),
        'sound_file': (body.get('sound_file') or 'alarm').strip(),
        'chime_on_home': bool(body.get('chime_on_home', True)),
    }
    _set_db_setting(f'zone_{zone_id}_alarm', json.dumps(settings))
    return jsonify(settings)


# ── Sound Files Management ────────────────────────────────────────────────────

SOUND_DIR = os.getenv("SOUND_DIR", "/data/sounds")


@app.route('/api/sounds', methods=['GET'])
def list_sounds():
    """List available sound files."""
    os.makedirs(SOUND_DIR, exist_ok=True)
    files = []
    for fname in sorted(os.listdir(SOUND_DIR)):
        if fname.lower().endswith(('.mp3', '.wav', '.ogg')):
            files.append({'name': os.path.splitext(fname)[0], 'filename': fname})
    # Always include built-in options
    builtins = [
        {'name': 'alarm', 'filename': '__builtin_alarm', 'builtin': True},
        {'name': 'chime', 'filename': '__builtin_chime', 'builtin': True},
    ]
    return jsonify(builtins + files)


@app.route('/api/sounds', methods=['POST'])
def upload_sound():
    """Upload a custom sound file."""
    os.makedirs(SOUND_DIR, exist_ok=True)
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Empty filename'}), 400
    # Sanitize filename - strip path separators and only allow safe chars
    base_name = os.path.basename(f.filename)
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', base_name)
    if not safe_name.lower().endswith(('.mp3', '.wav', '.ogg')):
        return jsonify({'error': 'Only .mp3, .wav, .ogg files allowed'}), 400
    # Prevent path traversal
    if '..' in safe_name or '/' in safe_name:
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = os.path.join(SOUND_DIR, safe_name)
    # Verify resolved path is within SOUND_DIR
    if not os.path.realpath(filepath).startswith(os.path.realpath(SOUND_DIR)):
        return jsonify({'error': 'Invalid filename'}), 400
    f.save(filepath)
    return jsonify({'ok': True, 'name': os.path.splitext(safe_name)[0], 'filename': safe_name}), 201


@app.route('/api/sounds/<filename>', methods=['DELETE'])
def delete_sound(filename):
    """Delete a custom sound file."""
    if filename.startswith('__builtin'):
        return jsonify({'error': 'Cannot delete built-in sounds'}), 400
    # Sanitize and validate filename
    base_name = os.path.basename(filename)
    safe_name = re.sub(r'[^a-zA-Z0-9_\-.]', '_', base_name)
    if '..' in safe_name or '/' in safe_name:
        return jsonify({'error': 'Invalid filename'}), 400
    filepath = os.path.join(SOUND_DIR, safe_name)
    # Verify resolved path is within SOUND_DIR
    if not os.path.realpath(filepath).startswith(os.path.realpath(SOUND_DIR)):
        return jsonify({'error': 'Invalid filename'}), 400
    if os.path.exists(filepath):
        os.remove(filepath)
    return '', 204


# ── Performance Monitoring ────────────────────────────────────────────────────

_perf_lock = threading.Lock()
_perf_prev_cpu = None


@app.route('/api/performance', methods=['GET'])
def get_performance():
    """Get server and NVR performance metrics."""
    global _perf_prev_cpu
    import platform

    # Server metrics
    server_metrics = {
        'hostname': platform.node(),
        'platform': platform.system(),
    }

    # CPU usage (simple /proc/stat parse or fallback)
    try:
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        parts = line.split()
        idle = int(parts[4])
        total = sum(int(p) for p in parts[1:])
        # Store for delta calculation (thread-safe)
        with _perf_lock:
            if _perf_prev_cpu is None:
                _perf_prev_cpu = (idle, total)
                cpu_percent = 0.0
            else:
                prev_idle, prev_total = _perf_prev_cpu
                d_idle = idle - prev_idle
                d_total = total - prev_total
                cpu_percent = round((1.0 - d_idle / max(d_total, 1)) * 100, 1)
                _perf_prev_cpu = (idle, total)
        server_metrics['cpu_percent'] = cpu_percent
    except Exception:
        server_metrics['cpu_percent'] = None

    # Memory usage
    try:
        with open('/proc/meminfo', 'r') as f:
            mem = {}
            for line in f:
                parts = line.split()
                if parts[0].rstrip(':') in ('MemTotal', 'MemAvailable', 'MemFree'):
                    mem[parts[0].rstrip(':')] = int(parts[1]) * 1024  # kB to bytes
        total_mem = mem.get('MemTotal', 0)
        avail_mem = mem.get('MemAvailable', mem.get('MemFree', 0))
        used_mem = total_mem - avail_mem
        server_metrics['mem_total'] = total_mem
        server_metrics['mem_used'] = used_mem
        server_metrics['mem_percent'] = round(used_mem / max(total_mem, 1) * 100, 1)
    except Exception:
        server_metrics['mem_total'] = None
        server_metrics['mem_used'] = None
        server_metrics['mem_percent'] = None

    # Disk usage (data volume)
    try:
        st = os.statvfs('/data')
        total_disk = st.f_blocks * st.f_frsize
        free_disk = st.f_bavail * st.f_frsize
        used_disk = total_disk - free_disk
        server_metrics['disk_total'] = total_disk
        server_metrics['disk_used'] = used_disk
        server_metrics['disk_percent'] = round(used_disk / max(total_disk, 1) * 100, 1)
    except Exception:
        server_metrics['disk_total'] = None
        server_metrics['disk_used'] = None
        server_metrics['disk_percent'] = None

    # Uptime
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_secs = float(f.read().split()[0])
        server_metrics['uptime_seconds'] = int(uptime_secs)
    except Exception:
        server_metrics['uptime_seconds'] = None

    # NVR metrics (from NVR API)
    nvr_metrics = {'reachable': False}
    try:
        user, passwd = _get_nvr_event_creds()
        resp = requests.get(
            f"http://{DVR_HOST}:{DVR_HTTP_PORT}/cgi-bin/magicBox.cgi?action=getMemoryInfo",
            auth=HTTPDigestAuth(user, passwd),
            timeout=5,
        )
        if resp.status_code == 200:
            nvr_metrics['reachable'] = True
            kv = _parse_dahua_kv_text(resp.text)
            nvr_metrics['mem_total'] = int(kv.get('status.Total', 0))
            nvr_metrics['mem_used'] = int(kv.get('status.Used', 0))

        # CPU
        resp2 = requests.get(
            f"http://{DVR_HOST}:{DVR_HTTP_PORT}/cgi-bin/magicBox.cgi?action=getCPUUsage",
            auth=HTTPDigestAuth(user, passwd),
            timeout=5,
        )
        if resp2.status_code == 200:
            kv2 = _parse_dahua_kv_text(resp2.text)
            nvr_metrics['cpu_percent'] = float(kv2.get('status.CPUUsage', kv2.get('usage', 0)))
    except Exception as e:
        nvr_metrics['error'] = str(e)

    return jsonify({
        'server': server_metrics,
        'nvr': nvr_metrics,
    })


_nvr_thread = threading.Thread(target=_nvr_event_worker, daemon=True, name="nvr-events")
_nvr_thread.start()

init_db()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
