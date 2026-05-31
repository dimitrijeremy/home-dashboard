#!/usr/bin/env python3
"""
analyzer.py — AI Video Analysis Service (On-Demand Optimized)

Capabilities:
  - Person detection (YOLOv8n) on RTSP streams from MediaMTX
  - Perimeter zone intrusion detection (polygon zones from backend DB)
  - Face recognition (InsightFace ArcFace) with enrollment via backend API
  - Saves per-channel snapshots for the UI zone editor
  - Posts detection events to backend /api/analyzer-event

On-Demand Behavior:
  - When mode=home: only saves snapshots (lightweight), skips AI inference
  - When mode=away: full AI processing (YOLO + face detection)
  - Frame rate adapts based on mode for efficiency

Environment variables:
  BACKEND_URL    — default http://backend:5000
  MTX_RTSP       — default rtsp://host.docker.internal:8554
  SNAPSHOT_DIR   — default /data/snapshots
  PROCESS_EVERY  — process 1 out of N frames (default 8, ~2fps from 15fps sub-stream)
  FACE_THRESH    — cosine similarity threshold for face match (default 0.40)
  ZONE_CONF      — YOLO confidence threshold (default 0.40)
  COOLDOWN_SECS  — seconds before repeating same event (default 20)
  SNAPSHOT_EVERY — save snapshot every N frames even without AI (default 30)
"""

import io, json, logging, os, time, threading
import cv2
import numpy as np
import requests
from datetime import datetime, timezone
from PIL import Image
from shapely.geometry import Point, Polygon as SPolygon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("analyzer")

# ── Config ────────────────────────────────────────────────────────────────────
BACKEND_URL   = os.getenv("BACKEND_URL",   "http://backend:5000")
MTX_RTSP_BASE = os.getenv("MTX_RTSP",     "rtsp://host.docker.internal:8554")
SNAPSHOT_DIR  = os.getenv("SNAPSHOT_DIR", "/data/snapshots")
PROCESS_EVERY = int(os.getenv("PROCESS_EVERY", "8"))
FACE_THRESH   = float(os.getenv("FACE_THRESH", "0.40"))
ZONE_CONF     = float(os.getenv("ZONE_CONF",   "0.40"))
COOLDOWN_SECS = int(os.getenv("COOLDOWN_SECS", "20"))
REFRESH_SECS  = int(os.getenv("REFRESH_SECS",  "30"))
SNAPSHOT_EVERY = int(os.getenv("SNAPSHOT_EVERY", "30"))  # snapshot-only cadence

os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# ── HTTP Session (connection pooling) ─────────────────────────────────────────
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})

# ── Lazy Model Loading ────────────────────────────────────────────────────────
# Models are loaded on first use instead of at startup to allow
# the container to start quickly and serve snapshots immediately.
_yolo = None
_face_app = None
_models_lock = threading.Lock()
_models_loaded = False


def _ensure_models():
    """Load AI models on first demand (lazy initialization)."""
    global _yolo, _face_app, _models_loaded
    if _models_loaded:
        return
    with _models_lock:
        if _models_loaded:
            return
        log.info("Loading YOLOv8n model…")
        from ultralytics import YOLO
        _yolo = YOLO("yolov8n.pt")
        _yolo.fuse()
        log.info("YOLOv8n ready.")

        log.info("Loading InsightFace buffalo_sc…")
        from insightface.app import FaceAnalysis
        _face_app = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
        _face_app.prepare(ctx_id=0, det_size=(320, 320))
        log.info("InsightFace ready.")
        _models_loaded = True

# ── Shared State ──────────────────────────────────────────────────────────────
# {face_id: {"name": str, "embedding": np.ndarray}}
face_db: dict = {}
face_db_lock  = threading.Lock()

# {camera_id: [{"id": int, "name": str, "poly": SPolygon}]}
zone_db: dict = {}
zone_db_lock  = threading.Lock()

# {(channel_id, key): last_triggered_ts}
cooldowns: dict = {}
cooldown_lock   = threading.Lock()

# Current mode from backend (home/away) — controls AI processing
_current_mode = "home"
_mode_lock    = threading.Lock()


def _get_mode() -> str:
    with _mode_lock:
        return _current_mode


def _set_mode(mode: str):
    global _current_mode
    with _mode_lock:
        _current_mode = mode


# ── Helpers ───────────────────────────────────────────────────────────────────
def _cooling(channel_id: str, key: str) -> bool:
    """Returns True if still in cooldown. Side-effect: starts cooldown if not."""
    ck = (channel_id, key)
    with cooldown_lock:
        if time.time() - cooldowns.get(ck, 0) < COOLDOWN_SECS:
            return True
        cooldowns[ck] = time.time()
        return False


def _post_event(channel_id: str, camera_name: str, event_type: str,
                zone_name=None, person_name=None, confidence=None):
    try:
        _session.post(
            f"{BACKEND_URL}/api/analyzer-event",
            json={
                "channel_id":  channel_id,
                "camera_name": camera_name,
                "event_type":  event_type,
                "zone_name":   zone_name,
                "person_name": person_name,
                "confidence":  round(confidence, 3) if confidence else None,
            },
            timeout=4,
        )
    except Exception as e:
        log.warning(f"post_event failed: {e}")


def _identify_person(frame_bgr: np.ndarray, x1, y1, x2, y2) -> tuple[str | None, float]:
    """Crop person bounding box, run face detection + recognition.
    Returns (name_or_None, confidence). None means no face found or unknown."""
    _ensure_models()
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[max(0, y1 - 10):min(h, y2 + 10),
                     max(0, x1 - 10):min(w, x2 + 10)]
    if crop.size == 0:
        return None, 0.0
    try:
        faces = _face_app.get(crop)
        if not faces:
            return None, 0.0
        emb = faces[0].normed_embedding
        best_name, best_sim = None, 0.0
        with face_db_lock:
            for data in face_db.values():
                sim = float(np.dot(emb, data["embedding"]))
                if sim > best_sim:
                    best_sim, best_name = sim, data["name"]
        if best_sim >= FACE_THRESH:
            return best_name, best_sim
        return None, best_sim   # face found but unrecognised
    except Exception as e:
        log.debug(f"identify_person: {e}")
        return None, 0.0


# ── DB Refresh ────────────────────────────────────────────────────────────────
def refresh_face_db():
    try:
        r = _session.get(f"{BACKEND_URL}/api/faces?include_photo=1", timeout=10)
        if r.status_code != 200:
            return
        faces_data = r.json()
        new_db: dict = {}
        for fd in faces_data:
            photo_b64 = fd.get("photo_b64")
            if not photo_b64:
                continue
            try:
                import base64
                img_bytes = base64.b64decode(photo_b64)
                img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                img_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                _ensure_models()
                result = _face_app.get(img_bgr)
                if not result:
                    log.warning(f"No face found in enrolled photo: {fd['name']}")
                    continue
                new_db[fd["id"]] = {
                    "name": fd["name"],
                    "embedding": result[0].normed_embedding,
                }
            except Exception as e:
                log.warning(f"Error enrolling face id={fd['id']}: {e}")

        with face_db_lock:
            face_db.clear()
            face_db.update(new_db)
        log.info(f"Face DB: {len(face_db)} known faces")
    except Exception as e:
        log.warning(f"refresh_face_db: {e}")


def refresh_zone_db():
    try:
        r = _session.get(f"{BACKEND_URL}/api/zones", timeout=8)
        if r.status_code != 200:
            return
        new_db: dict = {}
        for z in r.json():
            if not z.get("enabled", True):
                continue
            try:
                pts = json.loads(z["points_json"])
                if len(pts) < 3:
                    continue
                poly = SPolygon(pts)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                cam_id = z["camera_id"]
                new_db.setdefault(cam_id, []).append({
                    "id":   z["id"],
                    "name": z["name"],
                    "poly": poly,
                })
            except Exception as e:
                log.warning(f"Zone {z.get('id')} parse error: {e}")

        with zone_db_lock:
            zone_db.clear()
            zone_db.update(new_db)
        zone_count = sum(len(v) for v in zone_db.values())
        log.info(f"Zone DB: {zone_count} zones across {len(zone_db)} cameras")
    except Exception as e:
        log.warning(f"refresh_zone_db: {e}")


def refresh_mode():
    """Fetch current home/away mode from backend."""
    try:
        r = _session.get(f"{BACKEND_URL}/api/mode", timeout=5)
        if r.status_code == 200:
            mode = r.json().get("mode", "home")
            old = _get_mode()
            _set_mode(mode)
            if mode != old:
                log.info(f"Mode changed: {old} → {mode}")
                if mode == "away" and not _models_loaded:
                    log.info("Mode=away — loading AI models on demand…")
                    _ensure_models()
    except Exception as e:
        log.debug(f"refresh_mode: {e}")


def periodic_refresh():
    """Background thread: refresh face + zone DB + mode every REFRESH_SECS."""
    while True:
        time.sleep(REFRESH_SECS)
        refresh_mode()
        # Only refresh face/zone DBs when in away mode or models are loaded
        if _get_mode() == "away" or _models_loaded:
            refresh_face_db()
            refresh_zone_db()


# ── Frame Processing ───────────────────────────────────────────────────────────
def process_frame(camera_id: int, camera_name: str, channel_id: str,
                  frame: np.ndarray):
    """Run AI inference on a frame. Only called when mode=away."""
    _ensure_models()
    h, w = frame.shape[:2]
    results = _yolo(frame, classes=[0], conf=ZONE_CONF, verbose=False)
    if not results or len(results[0].boxes) == 0:
        return

    with zone_db_lock:
        cam_zones = list(zone_db.get(camera_id, []))

    has_face_db: bool
    with face_db_lock:
        has_face_db = len(face_db) > 0

    for box in results[0].boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
        conf = float(box.conf[0].cpu())
        cx_norm = ((x1 + x2) / 2) / w
        cy_norm = ((y1 + y2) / 2) / h
        foot_point = Point(cx_norm, min(1.0, y2 / h))

        # Track if face was already identified for this person (avoid duplicate calls)
        person_identified = False
        person_name = None
        face_conf = 0.0

        # ── Zone intrusion check ──────────────────────────────────────────
        for zone in cam_zones:
            if zone["poly"].covers(foot_point):
                if not _cooling(channel_id, f"zone_{zone['id']}"):
                    if has_face_db and not person_identified:
                        person_name, face_conf = _identify_person(frame, x1, y1, x2, y2)
                        person_identified = True
                    log.info(
                        f"[{channel_id}] ZONE '{zone['name']}' "
                        f"person={person_name or 'unknown'} conf={conf:.2f}"
                    )
                    _post_event(
                        channel_id, camera_name, "ZoneIntrusion",
                        zone_name=zone["name"],
                        person_name=person_name,
                        confidence=conf,
                    )
                break  # one zone per person per frame is enough

        # ── Face recognition (only if not already done above) ─────────────
        if has_face_db and not person_identified:
            person_name, face_conf = _identify_person(frame, x1, y1, x2, y2)
            person_identified = True

        if person_identified and person_name and not _cooling(channel_id, f"face_known_{person_name}"):
            log.info(f"[{channel_id}] FaceRecognized: {person_name} ({face_conf:.2f})")
            _post_event(channel_id, camera_name, "FaceRecognized",
                        person_name=person_name, confidence=face_conf)
        elif person_identified and person_name is None and 0 < face_conf < FACE_THRESH:
            # face detected but unrecognised
            if not _cooling(channel_id, f"face_unknown_{int(cx_norm*8)}_{int(cy_norm*8)}"):
                log.info(f"[{channel_id}] UnknownFace sim={face_conf:.2f}")
                _post_event(channel_id, camera_name, "UnknownFace",
                            confidence=face_conf)


# ── Channel Worker ─────────────────────────────────────────────────────────────
def channel_worker(camera_id: int, camera_name: str, channel_id: str):
    rtsp_url = f"{MTX_RTSP_BASE}/{channel_id}"
    log.info(f"Worker start: {channel_id} → {rtsp_url}")
    frame_count = 0

    while True:
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

        if not cap.isOpened():
            log.warning(f"{channel_id}: RTSP open failed, retry in 10s")
            time.sleep(10)
            continue

        log.info(f"{channel_id}: stream opened OK")
        try:
            snap_path = os.path.join(SNAPSHOT_DIR, f"{channel_id}.jpg")
            while True:
                ret, frame = cap.read()
                if not ret:
                    log.warning(f"{channel_id}: read failed, reconnecting…")
                    break

                frame_count += 1
                mode = _get_mode()

                # Always save snapshot periodically (lightweight, for UI zone editor)
                if frame_count % SNAPSHOT_EVERY == 0 and not (mode == "away" and frame_count % PROCESS_EVERY == 0):
                    cv2.imwrite(snap_path, frame)

                # AI processing only when mode=away and at PROCESS_EVERY cadence
                if mode == "away" and frame_count % PROCESS_EVERY == 0:
                    cv2.imwrite(snap_path, frame)  # fresh snap before AI
                    process_frame(camera_id, camera_name, channel_id, frame)

        except Exception as e:
            log.error(f"{channel_id} worker error: {e}")
        finally:
            cap.release()

        time.sleep(5)


# ── Camera Discovery + Thread Management ──────────────────────────────────────
_workers: dict[str, threading.Thread] = {}


def sync_workers():
    """Start/stop worker threads to match cameras from backend."""
    try:
        r = _session.get(f"{BACKEND_URL}/api/cameras", timeout=8)
        if r.status_code != 200:
            return
        cameras = r.json()
    except Exception as e:
        log.warning(f"sync_workers fetch error: {e}")
        return

    active_channels = set()
    for cam in cameras:
        from urllib.parse import urlsplit
        path_seg = urlsplit(cam["stream_url"]).path.strip("/").split("/")[0]
        if not path_seg:
            continue
        active_channels.add(path_seg)
        if path_seg not in _workers or not _workers[path_seg].is_alive():
            t = threading.Thread(
                target=channel_worker,
                args=(cam["id"], cam["name"], path_seg),
                daemon=True,
                name=f"worker-{path_seg}",
            )
            _workers[path_seg] = t
            t.start()
            log.info(f"Started worker thread for {path_seg}")

    # Threads for removed cameras will exit gracefully (stream will close)
    for ch in list(_workers.keys()):
        if ch not in active_channels:
            del _workers[ch]
            log.info(f"Removed worker reference for {ch}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info(f"Analyzer starting — backend={BACKEND_URL}, mediaMTX={MTX_RTSP_BASE}")
    log.info(f"On-demand mode: AI models load lazily on first mode=away")

    # Initial DB load (wait for backend to be ready)
    for attempt in range(15):
        try:
            r = _session.get(f"{BACKEND_URL}/api/cameras", timeout=5)
            if r.status_code == 200:
                break
        except Exception:
            pass
        log.info(f"Waiting for backend… ({attempt + 1}/15)")
        time.sleep(4)

    refresh_mode()
    refresh_zone_db()

    # Only load face DB if mode=away (models needed)
    if _get_mode() == "away":
        _ensure_models()
        refresh_face_db()

    # Start background refresh thread
    threading.Thread(target=periodic_refresh, daemon=True, name="refresher").start()

    # Start workers for existing cameras
    sync_workers()

    # Main loop: re-sync workers every 60s (picks up added/removed cameras)
    while True:
        time.sleep(60)
        refresh_mode()
        if _get_mode() == "away" or _models_loaded:
            refresh_face_db()
            refresh_zone_db()
        sync_workers()


if __name__ == "__main__":
    main()
