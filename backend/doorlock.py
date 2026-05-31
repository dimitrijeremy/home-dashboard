"""
Paloma DLP6202 Smart Door Lock integration via Tuya Cloud API.

Features:
- Remote lock/unlock
- Camera live stream (HLS URL allocation)
- Two-way audio (speak/listen via WebRTC signaling)
- Doorbell / alert event streaming
- Device status (battery, lock state, etc.)
"""

import json
import threading
import time
from datetime import datetime

import requests

# Try to import tuya-connector; gracefully degrade if not installed
try:
    from tuya_connector import TuyaOpenAPI, TUYA_LOGGER
    TUYA_AVAILABLE = True
except ImportError:
    TUYA_AVAILABLE = False


# Tuya API region endpoints
TUYA_ENDPOINTS = {
    'us': 'https://openapi.tuyaus.com',
    'eu': 'https://openapi.tuyaeu.com',
    'cn': 'https://openapi.tuyacn.com',
    'in': 'https://openapi.tuyain.com',
}

# Default DPs (Data Points) for Paloma DLP6202
# These may vary - users can override via config
DEFAULT_DP_MAP = {
    'unlock': 'unlock_fingerprint',       # DP code to remotely unlock
    'lock_state': 'closed_opened',        # DP for lock open/close state
    'battery': 'residual_electricity',    # DP for battery level
    'doorbell': 'doorbell',               # DP for doorbell press
    'alarm': 'alarm_lock',                # DP for alarm events
}


class TuyaDoorLock:
    """Manages connection and commands to Paloma DLP6202 via Tuya Cloud."""

    def __init__(self):
        self._api = None
        self._config = {}
        self._connected = False
        self._last_error = None
        self._device_status = {}
        self._events = []  # Recent doorlock events
        self._events_lock = threading.Lock()
        self._max_events = 50

    @property
    def connected(self):
        return self._connected

    @property
    def last_error(self):
        return self._last_error

    def configure(self, config):
        """
        Configure the Tuya connection.
        config: {
            'access_id': str,       # Tuya Cloud Project Access ID
            'access_secret': str,   # Tuya Cloud Project Access Secret
            'device_id': str,       # Device ID of the door lock
            'region': str,          # 'us', 'eu', 'cn', 'in'
            'uid': str,             # Tuya user UID (linked account)
        }
        """
        self._config = config
        self._connected = False
        self._last_error = None

        if not TUYA_AVAILABLE:
            self._last_error = "tuya-connector-python not installed"
            return False

        access_id = config.get('access_id', '').strip()
        access_secret = config.get('access_secret', '').strip()
        region = config.get('region', 'us').strip().lower()

        if not access_id or not access_secret:
            self._last_error = "Access ID and Secret are required"
            return False

        endpoint = TUYA_ENDPOINTS.get(region, TUYA_ENDPOINTS['us'])

        try:
            self._api = TuyaOpenAPI(endpoint, access_id, access_secret)
            resp = self._api.connect()
            if resp.get('success'):
                self._connected = True
                self._last_error = None
                return True
            else:
                self._last_error = resp.get('msg', 'Connection failed')
                return False
        except Exception as e:
            self._last_error = str(e)[:200]
            return False

    def _ensure_connected(self):
        if not self._connected or not self._api:
            raise RuntimeError(self._last_error or "Not connected to Tuya Cloud")

    def get_device_status(self):
        """Get current device status (lock state, battery, etc.)."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        resp = self._api.get(f'/v1.0/devices/{device_id}/status')
        if resp.get('success'):
            status_list = resp.get('result', [])
            status_map = {}
            for item in status_list:
                status_map[item.get('code', '')] = item.get('value')
            self._device_status = status_map
            return status_map
        else:
            raise RuntimeError(resp.get('msg', 'Failed to get device status'))

    def unlock(self):
        """Send remote unlock command."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        # Tuya smart lock remote unlock via password-free temporary key
        resp = self._api.post(
            f'/v1.0/devices/{device_id}/door-lock/password-free/open-door',
            body={}
        )

        if resp.get('success'):
            self._add_event('unlock', 'Remote unlock via dashboard')
            return True

        # Fallback: try direct command
        resp2 = self._api.post(
            f'/v1.0/devices/{device_id}/commands',
            body={'commands': [{'code': 'unlock_fingerprint', 'value': True}]}
        )
        if resp2.get('success'):
            self._add_event('unlock', 'Remote unlock via command')
            return True

        raise RuntimeError(resp.get('msg', 'Unlock failed'))

    def lock(self):
        """Send remote lock command (if supported)."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        resp = self._api.post(
            f'/v1.0/devices/{device_id}/commands',
            body={'commands': [{'code': 'reverse_lock', 'value': True}]}
        )
        if resp.get('success'):
            self._add_event('lock', 'Remote lock via dashboard')
            return True
        raise RuntimeError(resp.get('msg', 'Lock command failed'))

    def get_camera_stream(self):
        """
        Allocate a temporary camera stream URL from Tuya Cloud.
        Returns HLS/RTMP URL for the door lock camera.
        """
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        # Try IPC stream allocation
        resp = self._api.post(
            f'/v1.0/devices/{device_id}/stream/actions/allocate',
            body={'type': 'hls'}
        )

        if resp.get('success'):
            result = resp.get('result', {})
            return {
                'url': result.get('url', ''),
                'type': 'hls',
                'expire_time': result.get('expire_time', 0),
            }

        # Fallback: try RTMP
        resp2 = self._api.post(
            f'/v1.0/devices/{device_id}/stream/actions/allocate',
            body={'type': 'rtmp'}
        )
        if resp2.get('success'):
            result = resp2.get('result', {})
            return {
                'url': result.get('url', ''),
                'type': 'rtmp',
                'expire_time': result.get('expire_time', 0),
            }

        raise RuntimeError(resp.get('msg', 'Stream allocation failed'))

    def stop_camera_stream(self):
        """Deallocate/stop camera stream."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            return

        self._api.post(
            f'/v1.0/devices/{device_id}/stream/actions/deallocate',
            body={}
        )

    def start_talk(self):
        """
        Start two-way audio session.
        Returns WebRTC/audio session info for the frontend to establish connection.
        """
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        # Request audio talk session
        resp = self._api.post(
            f'/v1.0/devices/{device_id}/stream/actions/allocate',
            body={'type': 'talk'}
        )
        if resp.get('success'):
            return resp.get('result', {})

        # Some devices use different endpoint
        resp2 = self._api.post(
            f'/v1.0/devices/{device_id}/door-lock/actions/talk',
            body={'action': 'start'}
        )
        if resp2.get('success'):
            return resp2.get('result', {})

        raise RuntimeError(resp.get('msg', 'Talk session failed'))

    def stop_talk(self):
        """Stop two-way audio session."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            return
        self._api.post(
            f'/v1.0/devices/{device_id}/door-lock/actions/talk',
            body={'action': 'stop'}
        )

    def get_alerts(self, start_time=None, end_time=None, limit=20):
        """
        Get recent door lock alerts/events from Tuya Cloud.
        Includes: doorbell presses, unlock events, alarms, battery low, etc.
        """
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        # Get device logs for alerts
        resp = self._api.get(
            f'/v1.0/devices/{device_id}/logs',
            params={
                'type': '1,2,3,4,5,6,7',
                'size': str(limit),
                'start_time': str(start_time or ''),
                'end_time': str(end_time or ''),
            }
        )

        if resp.get('success'):
            logs = resp.get('result', {}).get('logs', [])
            return logs

        # Fallback to local events
        with self._events_lock:
            return list(self._events)

    def get_local_events(self):
        """Get locally tracked events."""
        with self._events_lock:
            return list(self._events)

    def _add_event(self, event_type, detail=''):
        """Add a local event record."""
        event = {
            'ts': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'type': event_type,
            'detail': detail,
        }
        with self._events_lock:
            self._events.insert(0, event)
            if len(self._events) > self._max_events:
                self._events = self._events[:self._max_events]

    def get_device_info(self):
        """Get device information."""
        self._ensure_connected()
        device_id = self._config.get('device_id', '').strip()
        if not device_id:
            raise ValueError("device_id not configured")

        resp = self._api.get(f'/v1.0/devices/{device_id}')
        if resp.get('success'):
            return resp.get('result', {})
        raise RuntimeError(resp.get('msg', 'Failed to get device info'))


# Singleton instance
doorlock = TuyaDoorLock()
