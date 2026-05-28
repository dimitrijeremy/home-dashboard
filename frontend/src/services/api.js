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
