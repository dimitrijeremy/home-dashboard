const BACKEND = import.meta.env.VITE_BACKEND_URL || ''

function url(path) {
  return BACKEND ? `${BACKEND}${path}` : path
}

// ── Auth-aware fetch ──────────────────────────────────────────────────────────
// Semua request API lewat sini: selalu kirim cookie sesi (credentials), dan
// beri tahu App kalau sesi sudah expired (401) supaya bisa redirect ke login
// tanpa tiap pemanggil harus cek sendiri.
let onUnauthorized = null
export function setUnauthorizedHandler(fn) { onUnauthorized = fn }

async function apiFetch(path, opts = {}) {
  const res = await fetch(url(path), { ...opts, credentials: 'include' })
  if (res.status === 401 && path !== '/api/login' && path !== '/api/session') {
    onUnauthorized?.()
  }
  return res
}

export async function fetchCameras() {
  const res = await apiFetch('/api/cameras')
  if (!res.ok) throw new Error('Failed to fetch cameras')
  return res.json()
}

export async function addCamera(name, rtsp_url, channel, ptz_supported = false) {
  const res = await apiFetch('/api/cameras', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, rtsp_url, channel, ptz_supported }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to add camera')
  }
  return res.json()
}

export async function updateCamera(id, body) {
  const res = await apiFetch(`/api/cameras/${id}`, {
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

export async function ptzCommand(id, action, code, speed = 4) {
  const res = await apiFetch(`/api/cameras/${id}/ptz`, {
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

export async function reorderCameras(order) {
  const res = await apiFetch('/api/cameras/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ order }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to reorder cameras')
  }
  return res.json()
}

export async function fetchServerStats() {
  const res = await apiFetch('/api/server-stats')
  if (!res.ok) throw new Error('Failed to fetch server stats')
  return res.json()
}

export async function deleteCamera(id) {
  const res = await apiFetch(`/api/cameras/${id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to delete camera')
  }
}

export async function fetchStreamStatus() {
  const res = await apiFetch('/api/stream-status')
  if (!res.ok) throw new Error('Failed to fetch stream status')
  return res.json()
}

export async function restartStream(camId) {
  const res = await apiFetch(`/api/stream-restart/${camId}`, { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to restart stream')
  }
  return res.json()
}

export async function restartAllStreams() {
  const res = await apiFetch('/api/stream-restart-all', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to restart all streams')
  }
  return res.json()
}

export async function fetchNvrEvents() {
  const res = await apiFetch('/api/nvr-events')
  if (!res.ok) throw new Error('Failed to fetch NVR events')
  return res.json()
}

export function nvrEventsStreamUrl() {
  return url('/api/nvr-events/stream')
}

// ── Zones ────────────────────────────────────────────────────────────────────

export async function fetchZones() {
  const res = await apiFetch('/api/zones')
  if (!res.ok) throw new Error('Failed to fetch zones')
  return res.json()
}

export async function addZone(camera_id, name, points) {
  const res = await apiFetch('/api/zones', {
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
  const res = await apiFetch(`/api/zones/${id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) throw new Error('Failed to delete zone')
}

export async function patchZone(id, body) {
  const res = await apiFetch(`/api/zones/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error('Failed to update zone')
  return res.json()
}

// ── Known Faces ───────────────────────────────────────────────────────────────

export async function fetchFaces() {
  const res = await apiFetch('/api/faces')
  if (!res.ok) throw new Error('Failed to fetch faces')
  return res.json()
}

export async function addFace(name, photoFile) {
  const fd = new FormData()
  fd.append('name', name)
  fd.append('photo', photoFile)
  const res = await apiFetch('/api/faces', { method: 'POST', body: fd })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to add face')
  }
  return res.json()
}

export async function deleteFace(id) {
  const res = await apiFetch(`/api/faces/${id}`, { method: 'DELETE' })
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
  const res = await apiFetch(`/api/detection-events?limit=${limit}&offset=${offset}`)
  if (!res.ok) throw new Error('Failed to fetch detection events')
  return res.json()
}

export async function clearDetectionEvents() {
  const res = await apiFetch('/api/detection-events', { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to clear events')
  return res.json()
}

// ── Home / Away Mode ──────────────────────────────────────────────────────────

export async function fetchMode() {
  const res = await apiFetch('/api/mode')
  if (!res.ok) throw new Error('Failed to fetch mode')
  return res.json()   // { mode: 'home' | 'away' }
}

export async function postMode(mode) {
  const res = await apiFetch('/api/mode', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!res.ok) throw new Error('Failed to set mode')
  return res.json()
}

// ── AI Global Toggle ──────────────────────────────────────────────────────────

export async function fetchAiConfig() {
  const res = await apiFetch('/api/ai-config')
  if (!res.ok) throw new Error('Failed to fetch AI config')
  return res.json()   // { enabled: boolean }
}

export async function postAiConfig(enabled) {
  const res = await apiFetch('/api/ai-config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error('Failed to set AI config')
  return res.json()
}

// ── NVR Config ────────────────────────────────────────────────────────────────

export async function fetchNvrConfig() {
  const res = await apiFetch('/api/nvr-config')
  if (!res.ok) throw new Error('Failed to fetch NVR config')
  return res.json()
}

export async function postNvrConfig(data) {
  const res = await apiFetch('/api/nvr-config', {
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
  const res = await apiFetch('/api/nvr-info')
  const data = await res.json().catch(() => ({}))
  if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to fetch NVR info')
  return data.info
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export async function fetchSession() {
  const res = await apiFetch('/api/session')
  if (!res.ok) throw new Error('Failed to fetch session')
  return res.json()   // { authenticated: boolean, username?: string }
}

export async function login(username, password) {
  const res = await apiFetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Login gagal')
  }
  return res.json()
}

export async function logout() {
  await apiFetch('/api/logout', { method: 'POST' })
}

export async function fetchUsers() {
  const res = await apiFetch('/api/users')
  if (!res.ok) throw new Error('Failed to fetch users')
  return res.json()
}

export async function createUser(username, password) {
  const res = await apiFetch('/api/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to create user')
  }
  return res.json()
}

export async function deleteUser(id) {
  const res = await apiFetch(`/api/users/${id}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to delete user')
  }
}

export async function fetchLoginLog(limit = 100) {
  const res = await apiFetch(`/api/login-log?limit=${limit}`)
  if (!res.ok) throw new Error('Failed to fetch login log')
  return res.json()
}

export async function fetchLoginWhitelist() {
  const res = await apiFetch('/api/login-whitelist')
  if (!res.ok) throw new Error('Failed to fetch login whitelist')
  return res.json()   // { cidrs: string, extra_env: string }
}

export async function saveLoginWhitelist(cidrs) {
  const res = await apiFetch('/api/login-whitelist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cidrs }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || 'Failed to save login whitelist')
  }
  return res.json()
}
