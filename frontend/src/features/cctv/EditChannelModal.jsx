import React, { useState } from 'react'
import { updateCamera } from '../../services/api'
import { buildRtspUrl } from './AddChannelModal'

// rtsps://user:pass@host:554/path?query → bagian-bagian yang bisa diedit
function parseRtsp(rtspUrl) {
  if (!rtspUrl) return { address: '', username: '', password: '' }
  try {
    const u = new URL(rtspUrl)
    return {
      address: `${u.protocol}//${u.host}${u.pathname}${u.search}`,
      username: decodeURIComponent(u.username || ''),
      password: decodeURIComponent(u.password || ''),
    }
  } catch {
    return { address: rtspUrl, username: '', password: '' }
  }
}

export default function EditChannelModal({ camera, onSaved, onClose }) {
  const isBuiltin = Boolean(camera.builtin)
  const parsed = parseRtsp(camera.rtsp_url)

  const [name,      setName]      = useState(camera.name || '')
  const [rtspUrl,   setRtspUrl]   = useState(parsed.address)
  const [username,  setUsername]  = useState(parsed.username)
  const [password,  setPassword]  = useState(parsed.password)
  const [channel,   setChannel]   = useState(String(camera.channel ?? ''))
  const [aiEnabled, setAiEnabled] = useState(camera.ai_enabled !== 0)
  const [ptzSupported, setPtzSupported] = useState(camera.ptz_supported === 1)
  const [error,     setError]     = useState('')
  const [loading,   setLoading]   = useState(false)

  const submit = async () => {
    const n = name.trim()
    if (!n) { setError('Nama tidak boleh kosong'); return }

    const body = { name: n, ai_enabled: aiEnabled, ptz_supported: ptzSupported }

    if (!isBuiltin && camera.rtsp_url) {
      const rtsp = rtspUrl.trim()
      const user = username.trim()
      const ch = channel.trim()
      if (!rtsp) { setError('Alamat RTSP tidak boleh kosong'); return }
      if (!user) { setError('Username tidak boleh kosong'); return }
      if (!password) { setError('Password tidak boleh kosong'); return }
      if (!/^\d+$/.test(ch) || Number(ch) <= 0) { setError('Channel harus angka lebih dari 0'); return }
      try {
        body.rtsp_url = buildRtspUrl(rtsp, user, password)
      } catch {
        setError('Alamat RTSP tidak valid'); return
      }
      body.channel = Number(ch)
    }

    setLoading(true)
    try {
      const updated = await updateCamera(camera.id, body)
      onSaved?.(updated)
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e) => {
    if (e.key === 'Enter') submit()
    if (e.key === 'Escape') onClose()
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" onKeyDown={handleKey}>
        <div className="modal-title">✎ Edit Channel — {camera.name}</div>

        <label>Nama Channel</label>
        <input
          autoFocus
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
        />

        {!isBuiltin && camera.rtsp_url && (
          <>
            <label>Alamat RTSP</label>
            <input
              value={rtspUrl}
              onChange={e => { setRtspUrl(e.target.value); setError('') }}
            />

            <label>Username</label>
            <input
              value={username}
              onChange={e => { setUsername(e.target.value); setError('') }}
            />

            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => { setPassword(e.target.value); setError('') }}
            />

            <label>Nomor Channel</label>
            <input
              inputMode="numeric"
              value={channel}
              onChange={e => { setChannel(e.target.value); setError('') }}
            />
          </>
        )}

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={aiEnabled}
            onChange={e => setAiEnabled(e.target.checked)}
            style={{ width: 'auto' }}
          />
          Deteksi AI aktif untuk kamera ini
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 6, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={ptzSupported}
            onChange={e => setPtzSupported(e.target.checked)}
            style={{ width: 'auto' }}
          />
          Kamera ini mendukung PTZ (pan/tilt/zoom)
        </label>
        <div className="modal-hint">
          Matikan deteksi AI pada kamera yang tidak penting untuk menghemat CPU —
          analyzer hanya memproses kamera dengan AI aktif. Kontrol PTZ hanya
          muncul di kamera yang ditandai mendukung PTZ — kebanyakan channel NVR
          fixed (bukan speed dome) tidak punya motor pan/tilt/zoom.
          {isBuiltin && ' Sumber RTSP kamera built-in diatur lewat menu Kredensial NVR.'}
        </div>

        {error && (
          <div style={{ color: 'var(--red)', fontSize: '.78rem', marginTop: 8 }}>⚠ {error}</div>
        )}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={loading}>Batal</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            {loading ? 'Menyimpan...' : 'Simpan'}
          </button>
        </div>
      </div>
    </div>
  )
}
