import React, { useEffect, useRef, useState } from 'react'
import { fetchNvrConfig } from '../../services/api'

const DEFAULT_RTSP = 'rtsps://10.10.30.2:554/cam/realmonitor?subtype=0&unicast=true&proto=Onvif&tls=true'
const DEFAULT_USERNAME = 'dashboard'
const DEFAULT_PASSWORD = 'd4$hb0ard-dlt'

function buildRtspUrl(address, username, password) {
  const base = address.includes('://') ? address : `rtsps://${address}`
  const url = new URL(base)
  const auth = `${username.trim()}:${password}@`
  return `${url.protocol}//${auth}${url.host}${url.pathname}${url.search}${url.hash}`
}

export default function AddChannelModal({ onAdd, onClose }) {
  const [name,    setName]    = useState('')
  const [rtspUrl, setRtspUrl] = useState(DEFAULT_RTSP)
  const [username, setUsername] = useState(DEFAULT_USERNAME)
  const [password, setPassword] = useState(DEFAULT_PASSWORD)
  const [channel, setChannel] = useState('5')
  const [error,   setError]   = useState('')
  const [loading, setLoading] = useState(false)
  const submitting = useRef(false)

  useEffect(() => {
    let alive = true
    fetchNvrConfig()
      .then(cfg => {
        if (!alive) return
        if (cfg.stream_user) setUsername(cfg.stream_user)
        if (cfg.stream_pass) setPassword(cfg.stream_pass)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const submit = async () => {
    if (submitting.current) return
    const n = name.trim()
    const rtsp = rtspUrl.trim()
    const user = username.trim()
    const ch = channel.trim()

    if (!n) { setError('Nama tidak boleh kosong'); return }
    if (!rtsp) { setError('Alamat RTSP tidak boleh kosong'); return }
    if (!user) { setError('Username tidak boleh kosong'); return }
    if (!password) { setError('Password tidak boleh kosong'); return }
    if (!/^\d+$/.test(ch) || Number(ch) <= 0) { setError('Channel harus angka lebih dari 0'); return }

    submitting.current = true
    setLoading(true)
    try {
      await onAdd(n, buildRtspUrl(rtsp, user, password), Number(ch))
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      submitting.current = false
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
        <div className="modal-title">➕ Tambah Channel CCTV</div>

        <label>Nama Channel</label>
        <input
          autoFocus
          placeholder="mis. Camera Belakang"
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
        />

        <label>Alamat RTSP</label>
        <input
          placeholder={DEFAULT_RTSP}
          value={rtspUrl}
          onChange={e => { setRtspUrl(e.target.value); setError('') }}
        />

        <label>Username</label>
        <input
          placeholder={DEFAULT_USERNAME}
          value={username}
          onChange={e => { setUsername(e.target.value); setError('') }}
        />

        <label>Password</label>
        <input
          type="password"
          placeholder={DEFAULT_PASSWORD}
          value={password}
          onChange={e => { setPassword(e.target.value); setError('') }}
        />

        <label>Nomor Channel</label>
        <input
          inputMode="numeric"
          placeholder="mis. 5"
          value={channel}
          onChange={e => { setChannel(e.target.value); setError('') }}
        />

        <div className="modal-hint">
          Isi alamat RTSP dasar tanpa username, password, dan tanpa `channel=`. Kamu juga bisa pakai placeholder <code style={{color:'var(--accent)'}}>{'{channel}'}</code>. Sistem akan membuat path HLS custom otomatis di MediaMTX.
        </div>

        {error && (
          <div style={{ color:'var(--red)', fontSize:'.78rem', marginTop:8 }}>⚠ {error}</div>
        )}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose} disabled={loading}>Batal</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            {loading ? 'Menyimpan...' : 'Tambah'}
          </button>
        </div>
      </div>
    </div>
  )
}
