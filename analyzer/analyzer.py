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
                zone_name=None, person_name=None, confidence=None, extra_json=None):
    try:
        requests.post(
            f"{BACKEND_URL}/api/analyzer-event",
            json={
                "channel_id":  channel_id,
                "camera_name": camera_name,
                "event_type":  event_type,
                "zone_name":   zone_name,
                "person_name": person_name,
                "confidence":  round(confidence, 3) if confidence else None,
                "extra_json":  json.dumps(extra_json) if extra_json else None,
            },
            timeout=4,
        )
    except Exception as e:
        log.warning(f"post_event failed: {e}")


def _extract_face_embedding(frame_bgr: np.ndarray, x1, y1, x2, y2) -> np.ndarray | None:
    """Crop person bounding box and return the first detected face embedding."""
    h, w = frame_bgr.shape[:2]
    crop = frame_bgr[max(0, y1 - 10):min(h, y2 + 10),
                     max(0, x1 - 10):min(w, x2 + 10)]
    if crop.size == 0:
        return None
    try:
        faces = face_app.get(crop)
        if not faces:
            return None
        return faces[0].normed_embedding
    except Exception as e:
        log.debug(f"extract_face_embedding: {e}")
        return None


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


def _identify_person(frame_bgr: np.ndarray, x1, y1, x2, y2) -> tuple[bool, str | None, float]:
    """Run face detection on a person crop and optionally match against enrolled faces."""
    embedding = _extract_face_embedding(frame_bgr, x1, y1, x2, y2)
    if embedding is None:
        return False, None, 0.0
    with face_db_lock:
        has_face_db = len(face_db) > 0
    if not has_face_db:
        return True, None, 0.0
    name, similarity = _match_face_embedding(embedding)
    return True, name, similarity


def _unknown_face_cooldown_key(channel_id: str, cx_norm: float, cy_norm: float) -> str:
    return f"face_unknown_{channel_id}_{int(cx_norm * 8)}_{int(cy_norm * 8)}"


def _emit_face_event(channel_id: str, camera_name: str, event_extra: dict,
                     cx_norm: float, cy_norm: float, face_found: bool,
                     person_name: str | None, face_conf: float):
    if not face_found:
        return
    if person_name and not _cooling(channel_id, f"face_known_{person_name}"):
        log.info(f"[{channel_id}] FaceRecognized: {person_name} ({face_conf:.2f})")
        _post_event(channel_id, camera_name, "FaceRecognized",
                    person_name=person_name, confidence=face_conf,
                    extra_json=event_extra)
        return
    if not person_name and not _cooling(channel_id, _unknown_face_cooldown_key(channel_id, cx_norm, cy_norm)):
        log.info(f"[{channel_id}] UnknownFace sim={face_conf:.2f}")
        _post_event(channel_id, camera_name, "UnknownFace",
                    confidence=face_conf,
                    extra_json=event_extra)


# ── DB Refresh ────────────────────────────────────────────────────────────────
def refresh_face_db():
    try:
        r = requests.get(f"{BACKEND_URL}/api/faces?include_photo=1", timeout=10)
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
        r = requests.get(f"{BACKEND_URL}/api/zones", timeout=8)
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
    results = yolo(frame, classes=[0], conf=ZONE_CONF, verbose=False)
    if not results or len(results[0].boxes) == 0:
        return

    with zone_db_lock:
        cam_zones = list(zone_db.get(camera_id, []))

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
        face_found, person_name, face_conf = _identify_person(frame, x1, y1, x2, y2)
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
        )


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

                # Save snapshot periodically (every PROCESS_EVERY frames = ~0.5–1s)
                if frame_count % PROCESS_EVERY == 0:
                    cv2.imwrite(snap_path, frame)
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
        r = requests.get(f"{BACKEND_URL}/api/cameras", timeout=8)
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

    # Initial DB load (wait for backend to be ready)
    for attempt in range(15):
        try:
            r = requests.get(f"{BACKEND_URL}/api/cameras", timeout=5)
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
