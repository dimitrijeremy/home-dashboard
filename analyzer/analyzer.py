#!/usr/bin/env python3
"""
analyzer.py — AI Video Analysis Service

Capabilities:
  - Person detection (YOLOv8n) on RTSP streams from MediaMTX
  - Perimeter zone intrusion detection (polygon zones from backend DB)
  - Face recognition (InsightFace ArcFace) with enrollment via backend API
  - Saves per-channel snapshots for the UI zone editor
  - Posts detection events to backend /api/analyzer-event

Environment variables:
  BACKEND_URL    — default http://backend:5000
  MTX_RTSP       — default rtsp://host.docker.internal:8554
  SNAPSHOT_DIR   — default /data/snapshots
  PROCESS_EVERY  — process 1 out of N frames (default 8, ~2fps from 15fps sub-stream)
  FACE_THRESH    — cosine similarity threshold for face match (default 0.40)
  ZONE_CONF      — YOLO confidence threshold (default 0.40)
  COOLDOWN_SECS  — seconds before repeating same event (default 20)
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
# Face recognition jauh lebih mahal dari YOLO — jalankan maksimal sekali per
# FACE_EVERY_SECS per channel, bukan pada setiap orang di setiap frame.
FACE_EVERY_SECS = float(os.getenv("FACE_EVERY_SECS", "2.0"))
# Ukuran inferensi YOLO; turunkan (mis. 480/416) untuk hemat CPU signifikan.
YOLO_IMGSZ    = int(os.getenv("YOLO_IMGSZ", "640"))

# /api/* backend sekarang butuh login browser ATAU token internal ini
# (server-to-server, dibaca dari file di volume /data yang sama dengan backend).
_INTERNAL_TOKEN_PATH = os.path.join(SNAPSHOT_DIR, "..", ".internal_token")


def _backend_headers():
    try:
        with open(_INTERNAL_TOKEN_PATH) as f:
            return {"X-Internal-Token": f.read().strip()}
    except OSError:
        return {}

os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# ── Model Loading (done once at startup) ─────────────────────────────────────
log.info("Loading YOLOv8n model…")
from ultralytics import YOLO
yolo = YOLO("yolov8n.pt")
yolo.fuse()
log.info("YOLOv8n ready.")

log.info("Loading InsightFace buffalo_sc…")
from insightface.app import FaceAnalysis
face_app = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
face_app.prepare(ctx_id=0, det_size=(320, 320))
log.info("InsightFace ready.")

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

# {channel_id: last_face_recognition_ts} — throttle face recognition
_face_last_run: dict = {}
_face_last_lock = threading.Lock()


def _face_due(channel_id: str) -> bool:
    now = time.time()
    with _face_last_lock:
        if now - _face_last_run.get(channel_id, 0) >= FACE_EVERY_SECS:
            _face_last_run[channel_id] = now
            return True
    return False


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
                zone_name=None, person_name=None, confidence=None, extra_json=None,
                face_photo: bytes | None = None):
    payload = {
        "channel_id":  channel_id,
        "camera_name": camera_name,
        "event_type":  event_type,
        "zone_name":   zone_name,
        "person_name": person_name,
        "confidence":  round(confidence, 3) if confidence else None,
        "extra_json":  json.dumps(extra_json) if extra_json else None,
    }
    if face_photo:
        import base64
        payload["face_photo_b64"] = base64.b64encode(face_photo).decode("ascii")
    try:
        requests.post(
            f"{BACKEND_URL}/api/analyzer-event",
            json=payload,
            headers=_backend_headers(),
            timeout=4,
        )
    except Exception as e:
        log.warning(f"post_event failed: {e}")


def _crop_face_jpeg(person_crop: np.ndarray, face_bbox) -> bytes | None:
    """Tight JPEG crop of just the face (with a little padding) for event photos."""
    ph, pw = person_crop.shape[:2]
    fx1, fy1, fx2, fy2 = [int(v) for v in face_bbox]
    pad = int(max(fx2 - fx1, fy2 - fy1, 1) * 0.3)
    fx1, fy1 = max(0, fx1 - pad), max(0, fy1 - pad)
    fx2, fy2 = min(pw, fx2 + pad), min(ph, fy2 + pad)
    face_crop = person_crop[fy1:fy2, fx1:fx2]
    if face_crop.size == 0:
        return None
    ok, buf = cv2.imencode(".jpg", face_crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


def _extract_face_embedding(frame_bgr: np.ndarray, x1, y1, x2, y2):
    """Crop person bounding box and return (embedding, face_jpeg_bytes) for the
    first detected face — both None if no face is found."""
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[max(0, y1 - 10):min(h, y2 + 10),
                     max(0, x1 - 10):min(w, x2 + 10)]
    if crop.size == 0:
        return None, None
    try:
        faces = face_app.get(crop)
        if not faces:
            return None, None
        face = faces[0]
        return face.normed_embedding, _crop_face_jpeg(crop, face.bbox)
    except Exception as e:
        log.debug(f"extract_face_embedding: {e}")
        return None, None


def _match_face_embedding(embedding: np.ndarray) -> tuple[str | None, float]:
    best_name, best_sim = None, 0.0
    with face_db_lock:
        for data in face_db.values():
            sim = float(np.dot(embedding, data["embedding"]))
            if sim > best_sim:
                best_sim, best_name = sim, data["name"]
    if best_name and best_sim >= FACE_THRESH:
        return best_name, best_sim
    return None, best_sim


def _identify_person(frame_bgr: np.ndarray, x1, y1, x2, y2):
    """Run face detection on a person crop and optionally match against enrolled
    faces. Returns (face_found, person_name, confidence, face_jpeg_bytes)."""
    embedding, face_photo = _extract_face_embedding(frame_bgr, x1, y1, x2, y2)
    if embedding is None:
        return False, None, 0.0, None
    with face_db_lock:
        has_face_db = len(face_db) > 0
    if not has_face_db:
        return True, None, 0.0, face_photo
    name, similarity = _match_face_embedding(embedding)
    return True, name, similarity, face_photo


def _unknown_face_cooldown_key(channel_id: str, cx_norm: float, cy_norm: float) -> str:
    return f"face_unknown_{channel_id}_{int(cx_norm * 8)}_{int(cy_norm * 8)}"


def _emit_face_event(channel_id: str, camera_name: str, event_extra: dict,
                     cx_norm: float, cy_norm: float, face_found: bool,
                     person_name: str | None, face_conf: float,
                     face_photo: bytes | None = None):
    if not face_found:
        return
    if person_name and not _cooling(channel_id, f"face_known_{person_name}"):
        log.info(f"[{channel_id}] FaceRecognized: {person_name} ({face_conf:.2f})")
        _post_event(channel_id, camera_name, "FaceRecognized",
                    person_name=person_name, confidence=face_conf,
                    extra_json=event_extra, face_photo=face_photo)
        return
    if not person_name and not _cooling(channel_id, _unknown_face_cooldown_key(channel_id, cx_norm, cy_norm)):
        log.info(f"[{channel_id}] UnknownFace sim={face_conf:.2f}")
        _post_event(channel_id, camera_name, "UnknownFace",
                    confidence=face_conf,
                    extra_json=event_extra, face_photo=face_photo)


# ── DB Refresh ────────────────────────────────────────────────────────────────
def refresh_face_db():
    try:
        r = requests.get(f"{BACKEND_URL}/api/faces?include_photo=1", headers=_backend_headers(), timeout=10)
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
                result = face_app.get(img_bgr)
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
        r = requests.get(f"{BACKEND_URL}/api/zones", headers=_backend_headers(), timeout=8)
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
                    "points": pts,
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


def periodic_refresh():
    """Background thread: refresh face + zone DB every REFRESH_SECS."""
    while True:
        time.sleep(REFRESH_SECS)
        refresh_face_db()
        refresh_zone_db()


# ── Frame Processing ───────────────────────────────────────────────────────────
def process_frame(camera_id: int, camera_name: str, channel_id: str,
                  frame: np.ndarray):
    h, w = frame.shape[:2]
    results = yolo(frame, classes=[0], conf=ZONE_CONF, imgsz=YOLO_IMGSZ, verbose=False)
    if not results or len(results[0].boxes) == 0:
        return

    with zone_db_lock:
        cam_zones = list(zone_db.get(camera_id, []))

    run_face = _face_due(channel_id)

    for box in results[0].boxes:
        x1, y1, x2, y2 = map(int, box.xyxy[0].cpu().numpy())
        conf = float(box.conf[0].cpu())
        cx_norm = ((x1 + x2) / 2) / w
        cy_norm = ((y1 + y2) / 2) / h
        centroid = Point(cx_norm, cy_norm)
        foot_point = Point(cx_norm, min(1.0, y2 / h))
        bbox_norm = {
            "left": max(0.0, min(1.0, x1 / w)),
            "top": max(0.0, min(1.0, y1 / h)),
            "right": max(0.0, min(1.0, x2 / w)),
            "bottom": max(0.0, min(1.0, y2 / h)),
        }
        event_extra = {
            "detection_box": bbox_norm,
            "frame_size": {"width": w, "height": h},
        }
        if run_face:
            face_found, person_name, face_conf, face_photo = _identify_person(frame, x1, y1, x2, y2)
        else:
            face_found, person_name, face_conf, face_photo = False, None, 0.0, None
        if face_found:
            event_extra["face_detected"] = True

        # ── Zone intrusion check ──────────────────────────────────────────
        for zone in cam_zones:
            if zone["poly"].covers(foot_point):
                if not _cooling(channel_id, f"zone_{zone['id']}"):
                    log.info(
                        f"[{channel_id}] ZONE '{zone['name']}' "
                        f"person={person_name or 'unknown'} conf={conf:.2f}"
                    )
                    _post_event(
                        channel_id, camera_name, "ZoneIntrusion",
                        zone_name=zone["name"],
                        person_name=person_name,
                        confidence=conf,
                        extra_json={
                            **event_extra,
                            "zone_points": zone.get("points") or [],
                        },
                    )
                break  # one zone per person per frame is enough

        # ── Face recognition (anywhere in frame) ─────────────────────────
        _emit_face_event(
            channel_id,
            camera_name,
            event_extra,
            cx_norm,
            cy_norm,
            face_found,
            person_name,
            face_conf,
            face_photo,
        )


# ── Channel Worker ─────────────────────────────────────────────────────────────
def channel_worker(camera_id: int, camera_name: str, channel_id: str,
                   stop_event: threading.Event):
    rtsp_url = f"{MTX_RTSP_BASE}/{channel_id}"
    log.info(f"Worker start: {channel_id} → {rtsp_url}")
    frame_count = 0

    while not stop_event.is_set():
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)

        if not cap.isOpened():
            cap.release()
            log.warning(f"{channel_id}: RTSP open failed, retry in 10s")
            if stop_event.wait(10):
                break
            continue

        log.info(f"{channel_id}: stream opened OK")
        try:
            snap_path = os.path.join(SNAPSHOT_DIR, f"{channel_id}.jpg")
            while not stop_event.is_set():
                ret, frame = cap.read()
                if not ret:
                    log.warning(f"{channel_id}: read failed, reconnecting…")
                    break

                frame_count += 1

                # Save snapshot periodically (every PROCESS_EVERY frames = ~0.5–1s)
                if frame_count % PROCESS_EVERY == 0:
                    cv2.imwrite(snap_path, frame)
                    process_frame(camera_id, camera_name, channel_id, frame)

        except Exception as e:
            log.error(f"{channel_id} worker error: {e}")
        finally:
            cap.release()

        stop_event.wait(5)

    log.info(f"Worker stop: {channel_id}")


# ── Camera Discovery + Thread Management ──────────────────────────────────────
# {channel_id: {"thread": Thread, "stop": Event}}
_workers: dict[str, dict] = {}


_ai_globally_off_logged = False


def _ai_globally_enabled() -> bool:
    """Master switch — kalau OFF, semua worker berhenti apa pun status
    ai_enabled per-kamera. Dipakai untuk fokus ke streaming saat server berat.
    """
    global _ai_globally_off_logged
    try:
        r = requests.get(f"{BACKEND_URL}/api/ai-config", headers=_backend_headers(), timeout=5)
        enabled = bool(r.json().get("enabled", True)) if r.status_code == 200 else True
    except Exception as e:
        log.warning(f"ai-config fetch error, assuming enabled: {e}")
        enabled = True
    if not enabled and not _ai_globally_off_logged:
        log.info("AI globally OFF — stopping all detection workers")
        _ai_globally_off_logged = True
    elif enabled:
        _ai_globally_off_logged = False
    return enabled


def sync_workers():
    """Start/stop worker threads to match AI-enabled cameras from backend."""
    if not _ai_globally_enabled():
        for ch in list(_workers.keys()):
            _workers[ch]["stop"].set()
            del _workers[ch]
            log.info(f"Stopping worker for {ch} (AI globally off)")
        return

    try:
        r = requests.get(f"{BACKEND_URL}/api/cameras", headers=_backend_headers(), timeout=8)
        if r.status_code != 200:
            return
        cameras = r.json()
    except Exception as e:
        log.warning(f"sync_workers fetch error: {e}")
        return

    active_channels = set()
    for cam in cameras:
        # Hormati toggle AI per kamera dari dashboard (kolom ai_enabled di DB)
        if not cam.get("ai_enabled", 1):
            continue
        from urllib.parse import urlsplit
        path_seg = urlsplit(cam["stream_url"]).path.strip("/").split("/")[0]
        if not path_seg:
            continue
        active_channels.add(path_seg)
        entry = _workers.get(path_seg)
        if not entry or not entry["thread"].is_alive():
            stop_event = threading.Event()
            t = threading.Thread(
                target=channel_worker,
                args=(cam["id"], cam["name"], path_seg, stop_event),
                daemon=True,
                name=f"worker-{path_seg}",
            )
            _workers[path_seg] = {"thread": t, "stop": stop_event}
            t.start()
            log.info(f"Started worker thread for {path_seg}")

    # Beri sinyal stop untuk kamera yang dihapus / AI-nya dimatikan
    for ch in list(_workers.keys()):
        if ch not in active_channels:
            _workers[ch]["stop"].set()
            del _workers[ch]
            log.info(f"Stopping worker for {ch}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info(f"Analyzer starting — backend={BACKEND_URL}, mediaMTX={MTX_RTSP_BASE}")

    # Initial DB load (wait for backend to be ready)
    for attempt in range(15):
        try:
            r = requests.get(f"{BACKEND_URL}/api/cameras", headers=_backend_headers(), timeout=5)
            if r.status_code == 200:
                break
        except Exception:
            pass
        log.info(f"Waiting for backend… ({attempt + 1}/15)")
        time.sleep(4)

    refresh_face_db()
    refresh_zone_db()

    # Start background refresh thread
    threading.Thread(target=periodic_refresh, daemon=True, name="refresher").start()

    # Start workers for existing cameras
    sync_workers()

    # Main loop: re-sync workers every 60s (picks up added/removed cameras)
    while True:
        time.sleep(60)
        refresh_face_db()
        refresh_zone_db()
        sync_workers()


if __name__ == "__main__":
    main()
