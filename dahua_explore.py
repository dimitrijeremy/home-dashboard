#!/usr/bin/env python3
"""
dahua_explore.py — Eksplorasi kapabilitas Dahua NVR via CGI HTTP API + ONVIF.

Yang dicoba:
  1. Device info (model, firmware, serial)
  2. System info (waktu, uptime)
  3. Storage / disk status
  4. Channel / camera list
  5. Snapshot per channel
  6. Event types yang tersedia di NVR
  7. Long-poll event stream (motion, video loss, line crossing, dll)
  8. Recording search (daftar rekaman NVR)
  9. ONVIF device capabilities + event PullPoint
 10. Network config (IP, DNS)

Usage:
    python3 dahua_explore.py [--host HOST] [--user USER] [--pass PASS]
    python3 dahua_explore.py --probe         # info dasar saja, no event stream
    python3 dahua_explore.py --events        # mulai listen event stream (Ctrl+C to stop)
    python3 dahua_explore.py --snapshot 1    # simpan snapshot channel 1 ke /tmp/snap_ch1.jpg
    python3 dahua_explore.py --recordings    # cari rekaman 24 jam terakhir
    python3 dahua_explore.py --all           # semua kecuali event stream
"""
import sys
# Unbuffered output so long-running steps show progress immediately
sys.stdout.reconfigure(line_buffering=True)

import argparse
import base64
import hashlib
import http.client
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.request import HTTPDigestAuthHandler, HTTPPasswordMgrWithDefaultRealm, build_opener

# ─── Credentials ────────────────────────────────────────────────────────────
HOST     = os.getenv("DVR_HOST", "10.10.30.2")
HTTP_PORT= int(os.getenv("DVR_HTTP_PORT", "80"))
USERNAME = os.getenv("DVR_USER", "dashboard")
PASSWORD = os.getenv("DVR_PASS", "d4$hb0ard-dlt")
TIMEOUT  = 8
# ─── Helpers ────────────────────────────────────────────────────────────────

def _make_opener():
    """Build a urllib opener with Digest Auth pre-configured."""
    base_url = f"http://{HOST}:{HTTP_PORT}/"
    pm = HTTPPasswordMgrWithDefaultRealm()
    pm.add_password(None, base_url, USERNAME, PASSWORD)
    handler = HTTPDigestAuthHandler(pm)
    return build_opener(handler)


def _get(path: str, extra_headers: dict | None = None) -> tuple[int, str]:
    """HTTP GET with Digest Auth. Returns (status_code, body_str)."""
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    opener = _make_opener()
    req = urllib.request.Request(url)
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)


def _get_long(path: str, timeout: int = 30) -> tuple[int, str]:
    """Same as _get but with a longer timeout, for slow operations like recording search."""
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    opener = _make_opener()
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return -1, str(e)
    """Parse Dahua CGI key=value response into a dict.
    Handles dotted keys like 'SystemInfo.DeviceType=NVR'.
    """
    result = {}
    for line in body.strip().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def _print_kv(data: dict, indent: int = 2):
    pad = " " * indent
    for k, v in sorted(data.items()):
        print(f"{pad}{k} = {v}")


# ─── 1. Device Info ──────────────────────────────────────────────────────────

def probe_device_info():
    _print_section("1. Device Info")
    status, body = _get("/cgi-bin/magicBox.cgi?action=getDeviceType")
    if status == 200:
        print(f"  DeviceType: {body.strip()}")
    else:
        print(f"  getDeviceType → HTTP {status}")

    status, body = _get("/cgi-bin/magicBox.cgi?action=getHardwareVersion")
    if status == 200:
        print(f"  HardwareVersion: {body.strip()}")

    status, body = _get("/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
    if status == 200:
        for line in body.strip().splitlines()[:5]:
            print(f"  {line}")

    status, body = _get("/cgi-bin/magicBox.cgi?action=getSerialNo")
    if status == 200:
        print(f"  SerialNo response: {body.strip()[:120]}")

    # Alternate endpoint
    status, body = _get("/cgi-bin/devInfo.cgi?action=getDeviceType")
    if status == 200:
        kv = _parse_kv(body)
        _print_kv(kv)


# ─── 2. System Info ──────────────────────────────────────────────────────────

def probe_system_info():
    _print_section("2. System Info")
    status, body = _get("/cgi-bin/global.cgi?action=getSystemInfo")
    if status != 200:
        status, body = _get("/cgi-bin/SystemInfo.cgi?action=getSystemInfo")
    if status == 200:
        kv = _parse_kv(body)
        _print_kv(kv)
    else:
        print(f"  SystemInfo → HTTP {status}: {body[:200]}")

    # NVR time
    status, body = _get("/cgi-bin/global.cgi?action=getCurrentTime")
    if status == 200:
        print(f"  NVR Time: {body.strip()}")


# ─── 3. Storage / Disk ───────────────────────────────────────────────────────

def probe_storage():
    _print_section("3. Storage / Disk Status")
    status, body = _get("/cgi-bin/storagePoint.cgi?action=getStorageStatus")
    if status == 200:
        kv = _parse_kv(body)
        _print_kv(kv)
    else:
        print(f"  storagePoint → HTTP {status}")

    status, body = _get("/cgi-bin/diskManager.cgi?action=getDeviceAllInfo")
    if status == 200:
        # Could be long, print first 60 lines
        lines = body.strip().splitlines()
        for line in lines[:60]:
            print(f"  {line}")
        if len(lines) > 60:
            print(f"  ... ({len(lines)-60} more lines)")
    else:
        print(f"  diskManager → HTTP {status}")


# ─── 4. Channel / Camera List ────────────────────────────────────────────────

def probe_channels():
    _print_section("4. Channel / Camera Info")
    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle")
    if status == 200:
        kv = _parse_kv(body)
        _print_kv(kv)
    else:
        print(f"  ChannelTitle → HTTP {status}")

    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=VideoColor")
    if status == 200:
        lines = body.strip().splitlines()
        print(f"  VideoColor ({len(lines)} lines) — first 20:")
        for line in lines[:20]:
            print(f"    {line}")
    else:
        print(f"  VideoColor → HTTP {status}")


# ─── 5. Snapshot ─────────────────────────────────────────────────────────────

def take_snapshot(channel: int = 1, out_dir: str = "/tmp"):
    _print_section(f"5. Snapshot Channel {channel}")
    url = f"http://{HOST}:{HTTP_PORT}/cgi-bin/snapshot.cgi?channel={channel}"
    opener = _make_opener()
    try:
        with opener.open(url, timeout=10) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            data = resp.read()
            if "image" in content_type and data:
                out_path = os.path.join(out_dir, f"snap_ch{channel}.jpg")
                with open(out_path, "wb") as f:
                    f.write(data)
                print(f"  Saved {len(data)} bytes → {out_path}")
            else:
                print(f"  HTTP {status}, Content-Type: {content_type}")
                print(f"  Body: {data[:200]}")
    except Exception as e:
        print(f"  Error: {e}")


# ─── 6. Event Types ───────────────────────────────────────────────────────────

def probe_event_types():
    """Query all event codes available on this NVR."""
    _print_section("6. Event Types / Codes (MGR)")
    status, body = _get("/cgi-bin/eventManager.cgi?action=getEventIndexes&code=All")
    if status == 200:
        kv = _parse_kv(body)
        _print_kv(kv)
    else:
        print(f"  getEventIndexes → HTTP {status}")

    # Specific codes to probe:
    codes = [
        "VideoMotion", "VideoLoss", "VideoBlind", "VideoTamper",
        "AudioAnomaly",
        "CrossLineDetection", "CrossRegionDetection",
        "LoiterDetection", "ParkingDetection",
        "ObjectAbandon", "ObjectRemoval",
        "FaceDetection",
        "AlarmLocal",
        "DiskFull", "DiskError",
        "NetAbort",
        "AccessControl",
        "SmartMotionHuman", "SmartMotionVehicle",
    ]
    print("\n  Probing individual event codes (GET /cgi-bin/eventManager.cgi?action=getEventIndexes&code=X):")
    for code in codes:
        s, b = _get(f"/cgi-bin/eventManager.cgi?action=getEventIndexes&code={code}")
        if s == 200 and "index" in b.lower():
            print(f"    [OK]  {code}: {b.strip()[:100]}")
        elif s == 200:
            print(f"    [---] {code}: (no indexes / unsupported)")
        else:
            print(f"    [ERR] {code}: HTTP {s}")


# ─── 7. Event Stream (long-poll) ─────────────────────────────────────────────

def listen_events(duration_secs: int = 30, codes: str = "All"):
    """
    Dahua NVR menyediakan event stream via HTTP long-poll multipart/x-mixed-replace.
    Setiap event dikirim sebagai bagian multipart dengan body seperti:
        Code=VideoMotion; action=Start; index=0; data={...}

    codes: comma-separated event codes, e.g. "VideoMotion,CrossLineDetection" or "All"
    """
    _print_section(f"7. Event Stream (codes={codes}, listening {duration_secs}s — Ctrl+C to stop)")
    path = f"/cgi-bin/eventManager.cgi?action=attach&codes=[{codes}]&heartbeat=5"
    url = f"http://{HOST}:{HTTP_PORT}{path}"
    print(f"  URL: {url}")

    # Dahua event stream needs a real persistent HTTP connection.
    # We use http.client directly with Digest Auth flow.
    # Step 1: send unauthenticated, get 401 + WWW-Authenticate, compute Digest, re-send.
    import re

    def _digest_header(method: str, path: str, www_auth: str) -> str:
        """Build Authorization: Digest header from a WWW-Authenticate challenge."""
        realm_m = re.search(r'realm="([^"]+)"', www_auth)
        nonce_m = re.search(r'nonce="([^"]+)"', www_auth)
        realm = realm_m.group(1) if realm_m else ""
        nonce = nonce_m.group(1) if nonce_m else ""
        ha1 = hashlib.md5(f"{USERNAME}:{realm}:{PASSWORD}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{method}:{path}".encode()).hexdigest()
        response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        return (f'Digest username="{USERNAME}", realm="{realm}", '
                f'nonce="{nonce}", uri="{path}", response="{response}"')

    try:
        conn = http.client.HTTPConnection(HOST, HTTP_PORT, timeout=duration_secs + 10)
        # Step 1: probe for auth challenge
        conn.request("GET", path)
        r0 = conn.getresponse()
        r0.read()
        conn.close()

        auth_header = ""
        if r0.status == 401:
            www_auth = r0.getheader("WWW-Authenticate", "")
            if "Digest" in www_auth:
                auth_header = _digest_header("GET", path, www_auth)
            else:
                # Fallback to Basic
                token = base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
                auth_header = f"Basic {token}"
        
        # Step 2: real request with auth
        conn = http.client.HTTPConnection(HOST, HTTP_PORT, timeout=duration_secs + 10)
        headers = {}
        if auth_header:
            headers["Authorization"] = auth_header
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        print(f"  HTTP {resp.status} {resp.reason}")
        if resp.status != 200:
            print(f"  Body: {resp.read(500)}")
            return

        content_type = resp.getheader("Content-Type", "")
        print(f"  Content-Type: {content_type}")
        print(f"  (listening... Ctrl+C to stop)\n")

        buf = b""
        start = time.time()
        event_count = 0
        while time.time() - start < duration_secs:
            chunk = resp.read(1024)
            if not chunk:
                break
            buf += chunk
            # Dahua sends events delimited by --myboundary\r\n or similar
            while b"\r\n\r\n" in buf:
                sep = buf.find(b"\r\n\r\n")
                buf = buf[sep+4:]
                boundary_pos = buf.find(b"--myboundary")
                if boundary_pos == -1:
                    break
                payload = buf[:boundary_pos].decode("utf-8", errors="replace").strip()
                buf = buf[boundary_pos:]
                if payload and "Code=" in payload:
                    event_count += 1
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"  [{ts}] EVENT #{event_count}: {payload[:300]}")
                elif payload and "heartbeat" in payload.lower():
                    print(f"  [{datetime.now().strftime('%H:%M:%S')}] ♥ heartbeat")

        print(f"\n  Done. {event_count} event(s) received in {time.time()-start:.1f}s.")
    except KeyboardInterrupt:
        print("\n  Stopped by user.")
    except Exception as e:
        print(f"  Error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─── 8. Recording Search ─────────────────────────────────────────────────────

def probe_recordings(channel: int = 0, hours_back: int = 24):
    _print_section(f"8. Recording Search (ch{channel}, last {hours_back}h)")
    now = datetime.now()
    start_dt = now - timedelta(hours=hours_back)
    fmt = "%Y-%m-%d %H:%M:%S"

    # Dahua mediaFileFind uses stateful factory: create → findFile → findNextFile → close
    step1_s, step1_b = _get("/cgi-bin/mediaFileFind.cgi?action=factory.create")
    if step1_s != 200 or "object=" not in step1_b:
        print(f"  factory.create → HTTP {step1_s}: {step1_b[:200]}")
        return
    obj_id = step1_b.strip().split("=", 1)[1].strip()
    print(f"  factory.create → object={obj_id}")

    # findFile — channel=-1 means all channels; channel-specific uses 0-indexed
    chan_val = "-1" if channel == 0 else str(channel - 1)
    params = {
        "action": "findFile",
        "object": obj_id,
        "condition.Channel": chan_val,
        "condition.StartTime": start_dt.strftime(fmt),
        "condition.EndTime": now.strftime(fmt),
    }
    find_s, find_b = _get_long("/cgi-bin/mediaFileFind.cgi?" + urllib.parse.urlencode(params), timeout=30)
    print(f"  findFile → HTTP {find_s}: {find_b.strip()[:100]}")

    # findNextFile
    next_s, next_b = _get_long(f"/cgi-bin/mediaFileFind.cgi?action=findNextFile&object={obj_id}&count=20", timeout=30)
    if next_s == 200:
        lines = next_b.strip().splitlines()
        print(f"  findNextFile → {len(lines)} lines. First 30:")
        for line in lines[:30]:
            print(f"    {line}")
    else:
        print(f"  findNextFile → HTTP {next_s}: {next_b[:200]}")

    # Close session
    _get(f"/cgi-bin/mediaFileFind.cgi?action=close&object={obj_id}")


# ─── 9. ONVIF Capabilities + Event PullPoint ─────────────────────────────────

def probe_onvif():
    _print_section("9. ONVIF Capabilities + Event PullPoint")
    try:
        from onvif import ONVIFCamera
        from lxml import etree
    except ImportError:
        print("  onvif-zeep not installed. Run: pip3 install onvif-zeep --break-system-packages")
        return

    try:
        cam = ONVIFCamera(HOST, HTTP_PORT, USERNAME, PASSWORD, no_cache=True)
        print("  Connected to ONVIF camera.")

        # Device info
        dev_info = cam.devicemgmt.GetDeviceInformation()
        print(f"  Manufacturer:    {dev_info.Manufacturer}")
        print(f"  Model:           {dev_info.Model}")
        print(f"  FirmwareVersion: {dev_info.FirmwareVersion}")
        print(f"  SerialNumber:    {dev_info.SerialNumber}")
        print(f"  HardwareId:      {dev_info.HardwareId}")

        # Capabilities
        caps = cam.devicemgmt.GetCapabilities({'Category': 'All'})
        print(f"\n  Capabilities:")
        for svc in ('Events', 'PTZ', 'Imaging', 'Analytics', 'Media'):
            cap = getattr(caps, svc, None)
            if cap:
                xaddr = getattr(cap, 'XAddr', 'N/A')
                print(f"    {svc}: {xaddr}")

        # Event Pull-Point Subscription
        print(f"\n  Creating PullPointSubscription...")
        event_service = cam.create_events_service()
        pull_point = event_service.CreatePullPointSubscription({
            'InitialTerminationTime': 'PT2M',
        })
        pull_url = pull_point.SubscriptionReference.Address._value_1
        print(f"  PullPoint URL: {pull_url}")

        print(f"\n  Pulling events (5s window, max 100 messages)...")
        pull_service = cam.create_pullpoint_service()
        msg = pull_service.PullMessages({
            'MessageLimit': 100,
            'Timeout': 'PT5S',
        })
        notifications = msg.NotificationMessage if hasattr(msg, 'NotificationMessage') else []
        print(f"  Received {len(notifications)} notification(s).\n")

        # Parse using lxml (zeep stores Message as lxml element for non-standard types)
        tt_ns = "http://www.onvif.org/ver10/schema"
        topics_seen: dict[str, int] = {}

        for notif in notifications:
            # zeep wraps Message in CompoundValue; actual lxml element is in _value_1
            raw_msg = notif.Message
            msg_el = getattr(raw_msg, '_value_1', raw_msg)
            if msg_el is None or not hasattr(msg_el, 'tag'):
                continue

            ts        = msg_el.get('UtcTime', 'N/A')
            prop_op   = msg_el.get('PropertyOperation', 'N/A')
            # Source items → channel/rule info
            src_items  = {si.get('Name'): si.get('Value')
                          for si in msg_el.findall(f'.//{{{tt_ns}}}Source/{{{tt_ns}}}SimpleItem')}
            data_items = {si.get('Name'): si.get('Value')
                          for si in msg_el.findall(f'.//{{{tt_ns}}}Data/{{{tt_ns}}}SimpleItem')}
            # Topic text comes from XML raw; zeep doesn't deserialize it well for Dahua
            # Derive a readable key from src_items / data_items
            rule   = src_items.get('Rule', src_items.get('InputToken', '?'))
            is_mot = data_items.get('IsMotion', data_items.get('State', data_items.get('Value', '?')))
            vsrc   = src_items.get('VideoSourceConfigurationToken', '?')

            key = f"vsrc={vsrc} rule={rule} data={data_items}"
            if key not in topics_seen:
                topics_seen[key] = 0
                print(f"  [EVENT] UtcTime={ts}  Op={prop_op}")
                print(f"          Source: {src_items}")
                print(f"          Data:   {data_items}")
            topics_seen[key] += 1

        print(f"\n  Unique event signatures seen: {len(topics_seen)}")
        for k, cnt in topics_seen.items():
            print(f"    ({cnt}x) {k}")

    except Exception as e:
        print(f"  ONVIF error: {e}")


# ─── 10. Network Config ───────────────────────────────────────────────────────

def probe_network():
    _print_section("10. Network Config")
    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=Network")
    if status == 200:
        lines = body.strip().splitlines()
        # Filter useful lines
        interesting = [l for l in lines if any(
            kw in l for kw in ["IPAddress", "SubnetMask", "DefaultGateway", "DnsServer", "Hostname", "DefaultInterface"]
        )]
        for line in interesting[:20]:
            print(f"  {line}")
    else:
        print(f"  Network config → HTTP {status}")


# ─── 11. Alarm / IVS Config ───────────────────────────────────────────────────

def probe_alarm():
    _print_section("11. Alarm & IVS Rules")
    # VideoMotion alarm config
    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=VideoMotion")
    if status == 200:
        lines = body.strip().splitlines()
        print(f"  VideoMotion config ({len(lines)} lines), first 30:")
        for line in lines[:30]:
            print(f"    {line}")
    else:
        print(f"  VideoMotion → HTTP {status}")

    # IVS (Intelligent Video Surveillance) rules
    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule")
    if status == 200:
        lines = body.strip().splitlines()
        print(f"\n  IVS VideoAnalyseRule ({len(lines)} lines), first 30:")
        for line in lines[:30]:
            print(f"    {line}")
    else:
        print(f"\n  VideoAnalyseRule → HTTP {status}: {body[:200]}")

    # Smart motion (human/vehicle)
    status, body = _get("/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect")
    if status == 200:
        lines = body.strip().splitlines()
        print(f"\n  SmartMotionDetect ({len(lines)} lines):")
        for line in lines[:15]:
            print(f"    {line}")
    else:
        print(f"\n  SmartMotionDetect → HTTP {status}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_all_probe():
    probe_device_info()
    probe_system_info()
    probe_storage()
    probe_channels()
    probe_network()
    probe_event_types()
    probe_alarm()
    probe_recordings()
    probe_onvif()


def main():
    parser = argparse.ArgumentParser(description="Dahua NVR Explorer")
    parser.add_argument("--host",       default=None, help="NVR host (overrides DVR_HOST env)")
    parser.add_argument("--user",       default=None, help="Username (overrides DVR_USER env)")
    parser.add_argument("--password",   default=None, help="Password (overrides DVR_PASS env)")
    parser.add_argument("--probe",      action="store_true", help="Info dasar + event types")
    parser.add_argument("--events",     action="store_true", help="Listen event stream")
    parser.add_argument("--event-codes",default="All",       help="Event codes to subscribe (default: All)")
    parser.add_argument("--event-duration", type=int, default=60, help="Event listen duration in seconds")
    parser.add_argument("--snapshot",   type=int, default=0, metavar="CHANNEL", help="Take snapshot for channel N")
    parser.add_argument("--recordings", action="store_true", help="Search recordings last 24h")
    parser.add_argument("--all",        action="store_true", help="Semua probe kecuali event stream")
    args = parser.parse_args()

    global HOST, USERNAME, PASSWORD
    if args.host:     HOST = args.host
    if args.user:     USERNAME = args.user
    if args.password: PASSWORD = args.password

    print(f"Target: http://{HOST}:{HTTP_PORT}  User: {USERNAME}")

    if args.events:
        listen_events(duration_secs=args.event_duration, codes=args.event_codes)
    elif args.snapshot > 0:
        take_snapshot(args.snapshot)
    elif args.recordings:
        probe_recordings()
    elif args.probe:
        probe_device_info()
        probe_system_info()
        probe_channels()
        probe_event_types()
    elif args.all:
        run_all_probe()
    else:
        # Default: full probe
        run_all_probe()


if __name__ == "__main__":
    main()
