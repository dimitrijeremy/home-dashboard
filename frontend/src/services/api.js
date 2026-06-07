const BACKEND = import.meta.env.VITE_BACKEND_URL || ''

function url(path) {
  return BACKEND ? `${BACKEND}${path}` : path
}

export async function fetchCameras() {
  const res = await fetch(url('/api/cameras'))
  if (!res.ok) throw new Error('Failed to fetch cameras')
  return res.json()
}

export async function addCamera(name, rtsp_url, channel) {
  const res = await fetch(url('/api/cameras'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, rtsp_url, channel }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to add camera')
  }
  return res.json()
}

export async function deleteCamera(id) {
  const res = await fetch(url(`/api/cameras/${id}`), { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to delete camera')
  }
}

export async function updateCamera(id, body) {
  const res = await fetch(url(`/api/cameras/${id}`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to update camera')
  }
  return res.json()
}

export async function getCamera(id) {
  const res = await fetch(url(`/api/cameras/${id}`))
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to fetch camera')
  }
  return res.json()
}

export async function ptzCheck(id) {
  const res = await fetch(url(`/api/cameras/${id}/ptz-check`))
  if (!res.ok) return { supported: false }
  return res.json()
}

export async function ptzCommand(id, action, code, speed = 4) {
  const res = await fetch(url(`/api/cameras/${id}/ptz`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, code, speed }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'PTZ command failed')
  }
  return res.json()
}

export async function fetchStreamStatus() {
  const res = await fetch(url('/api/stream-status'))
  if (!res.ok) throw new Error('Failed to fetch stream status')
  return res.json()
}

export async function restartStream(camId) {
  const res = await fetch(url(`/api/stream-restart/${camId}`), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to restart stream')
  }
  return res.json()
}

export async function restartAllStreams() {
  const res = await fetch(url('/api/stream-restart-all'), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to restart all streams')
  }
  return res.json()
}

export async function fetchNvrEvents() {
  const res = await fetch(url('/api/nvr-events'))
  if (!res.ok) throw new Error('Failed to fetch NVR events')
  return res.json()
}

export async function fetchNvrHistory({ from, to, code, limit = 200 } = {}) {
  const params = new URLSearchParams()
  if (from)  params.set('from', from)
  if (to)    params.set('to', to)
  if (code)  params.set('code', code)
  params.set('limit', String(limit))
  const res = await fetch(url(`/api/nvr-events/history?${params.toString()}`))
  if (!res.ok) throw new Error('Failed to fetch NVR history')
  return res.json()
}

export async function fetchNvrPlaybackEvents({ from, to, code, channel = -1, limit = 80 } = {}) {
  const params = new URLSearchParams()
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  if (code) params.set('code', code)
  params.set('channel', String(channel))
  params.set('limit', String(limit))
  const res = await fetch(url(`/api/nvr-events/playback?${params.toString()}`))
  const data = await res.json().catch(() => ({}))
  if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to fetch NVR playback events')
  return data
}

export function nvrEventsStreamUrl() {
  return url('/api/nvr-events/stream')
}

// ── Zones ────────────────────────────────────────────────────────────────────

export async function fetchZones() {
  const res = await fetch(url('/api/zones'))
  if (!res.ok) throw new Error('Failed to fetch zones')
  return res.json()
}

export async function addZone(camera_id, name, points) {
  const res = await fetch(url('/api/zones'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ camera_id, name, points }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to add zone')
  }
  return res.json()
}

export async function deleteZone(id) {
  const res = await fetch(url(`/api/zones/${id}`), { method: 'DELETE' })
  if (!res.ok && res.status !== 204) throw new Error('Failed to delete zone')
}

export async function patchZone(id, body) {
  const res = await fetch(url(`/api/zones/${id}`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error('Failed to update zone')
  return res.json()
}

// ── Known Faces ───────────────────────────────────────────────────────────────

export async function fetchFaces() {
  const res = await fetch(url('/api/faces'))
  if (!res.ok) throw new Error('Failed to fetch faces')
  return res.json()
}

export async function addFace(name, photoFile) {
  const fd = new FormData()
  fd.append('name', name)
  fd.append('photo', photoFile)
  const res = await fetch(url('/api/faces'), { method: 'POST', body: fd })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to add face')
  }
  return res.json()
}

export async function deleteFace(id) {
  const res = await fetch(url(`/api/faces/${id}`), { method: 'DELETE' })
  if (!res.ok && res.status !== 204) throw new Error('Failed to delete face')
}

export function facePhotoUrl(id) {
  return url(`/api/faces/${id}/photo`)
}

export function cameraSnapshotUrl(camId) {
  return url(`/api/cameras/${camId}/snapshot?t=${Date.now()}`)
}

// ── Detection Events ──────────────────────────────────────────────────────────

export async function fetchDetectionEvents(limit = 50, offset = 0) {
  const res = await fetch(url(`/api/detection-events?limit=${limit}&offset=${offset}`))
  if (!res.ok) throw new Error('Failed to fetch detection events')
  return res.json()
}

export async function clearDetectionEvents() {
  const res = await fetch(url('/api/detection-events'), { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to clear events')
  return res.json()
}

// ── Home / Away Mode ──────────────────────────────────────────────────────────

export async function fetchMode() {
  const res = await fetch(url('/api/mode'))
  if (!res.ok) throw new Error('Failed to fetch mode')
  return res.json()   // { mode: 'home' | 'away' }
}

export async function postMode(mode) {
  const res = await fetch(url('/api/mode'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!res.ok) throw new Error('Failed to set mode')
  return res.json()
}

// ── NVR Config ────────────────────────────────────────────────────────────────

export async function fetchNvrConfig() {
  const res = await fetch(url('/api/nvr-config'))
  if (!res.ok) throw new Error('Failed to fetch NVR config')
  return res.json()
}

export async function postNvrConfig(data) {
  const res = await fetch(url('/api/nvr-config'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to save NVR config')
  }
  return res.json()
}

export async function fetchNvrInfo() {
  const res = await fetch(url('/api/nvr-info'))
  const data = await res.json().catch(() => ({}))
  if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to fetch NVR info')
  return data.info
}

// ── NVR Guard (Arm/Disarm) ────────────────────────────────────────────────────

export async function fetchNvrGuard() {
  const res = await fetch(url('/api/nvr-guard'))
  if (!res.ok) throw new Error('Failed to fetch NVR guard status')
  return res.json()
}

export async function postNvrGuard(armed) {
  const res = await fetch(url('/api/nvr-guard'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ armed }),
  })
  if (!res.ok) throw new Error('Failed to set NVR guard')
  return res.json()
}

// ── Siren/Speaker Control ─────────────────────────────────────────────────────

export async function triggerSiren(channel = 1) {
  const res = await fetch(url('/api/siren'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel }),
  })
  if (!res.ok) throw new Error('Failed to trigger siren')
  return res.json()
}

export async function stopSiren(channel = 1) {
  const res = await fetch(url('/api/siren/stop'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel }),
  })
  if (!res.ok) throw new Error('Failed to stop siren')
  return res.json()
}

// ── Siren Config ──────────────────────────────────────────────────────────────

export async function fetchSirenConfig() {
  const res = await fetch(url('/api/siren-config'))
  if (!res.ok) throw new Error('Failed to fetch siren config')
  return res.json()
}

export async function postSirenConfig(data) {
  const res = await fetch(url('/api/siren-config'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to save siren config')
  return res.json()
}

// ── Zone Alarm Settings ───────────────────────────────────────────────────────

export async function fetchZoneAlarmSettings(zoneId) {
  const res = await fetch(url(`/api/zones/${zoneId}/alarm-settings`))
  if (!res.ok) throw new Error('Failed to fetch zone alarm settings')
  return res.json()
}

export async function postZoneAlarmSettings(zoneId, data) {
  const res = await fetch(url(`/api/zones/${zoneId}/alarm-settings`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to save zone alarm settings')
  return res.json()
}

// ── Sound Files ───────────────────────────────────────────────────────────────

export async function fetchSounds() {
  const res = await fetch(url('/api/sounds'))
  if (!res.ok) throw new Error('Failed to fetch sounds')
  return res.json()
}

export async function uploadSound(file) {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(url('/api/sounds'), { method: 'POST', body: fd })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to upload sound')
  }
  return res.json()
}

export async function deleteSound(filename) {
  const res = await fetch(url(`/api/sounds/${encodeURIComponent(filename)}`), { method: 'DELETE' })
  if (!res.ok && res.status !== 204) throw new Error('Failed to delete sound')
}

// ── Performance Monitoring ────────────────────────────────────────────────────

export async function fetchPerformance() {
  const res = await fetch(url('/api/performance'))
  if (!res.ok) throw new Error('Failed to fetch performance')
  return res.json()
}

// ── Door Lock (Paloma DLP6202 via Tuya) ───────────────────────────────────────

export async function fetchDoorlockConfig() {
  const res = await fetch(url('/api/doorlock/config'))
  if (!res.ok) throw new Error('Failed to fetch doorlock config')
  return res.json()
}

export async function postDoorlockConfig(data) {
  const res = await fetch(url('/api/doorlock/config'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to save doorlock config')
  }
  return res.json()
}

export async function fetchDoorlockStatus() {
  const res = await fetch(url('/api/doorlock/status'))
  if (!res.ok) throw new Error('Failed to fetch doorlock status')
  return res.json()
}

export async function doorlockUnlock() {
  const res = await fetch(url('/api/doorlock/unlock'), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to unlock')
  }
  return res.json()
}

export async function doorlockLock() {
  const res = await fetch(url('/api/doorlock/lock'), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to lock')
  }
  return res.json()
}

export async function doorlockCameraStream() {
  const res = await fetch(url('/api/doorlock/camera/stream'), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to get camera stream')
  }
  return res.json()
}

export async function doorlockCameraStop() {
  const res = await fetch(url('/api/doorlock/camera/stop'), { method: 'POST' })
  if (!res.ok) throw new Error('Failed to stop camera stream')
  return res.json()
}

export async function doorlockTalkStart() {
  const res = await fetch(url('/api/doorlock/talk/start'), { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to start talk')
  }
  return res.json()
}

export async function doorlockTalkStop() {
  const res = await fetch(url('/api/doorlock/talk/stop'), { method: 'POST' })
  if (!res.ok) throw new Error('Failed to stop talk')
  return res.json()
}

export async function fetchDoorlockAlerts(limit = 20) {
  const res = await fetch(url(`/api/doorlock/alerts?limit=${limit}`))
  if (!res.ok) throw new Error('Failed to fetch doorlock alerts')
  return res.json()
}

export async function fetchDoorlockInfo() {
  const res = await fetch(url('/api/doorlock/info'))
  if (!res.ok) throw new Error('Failed to fetch doorlock info')
  return res.json()
}
