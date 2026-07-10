import React, { useEffect, useRef, useState } from 'react'
import { fetchNvrConfig } from '../../services/api'

const RTSP_PLACEHOLDER = 'rtsps://<host-nvr>:554/cam/realmonitor?subtype=0&unicast=true&proto=Onvif&tls=true'

export function buildRtspUrl(address, username, password) {
  const base = address.includes('://') ? address : `rtsps://${address}`
  const url = new URL(base)
  const auth = `${username.trim()}:${password}@`
  return `${url.protocol}//${auth}${url.host}${url.pathname}${url.search}${url.hash}`
}

// Kamera IP PTZ berdiri sendiri (bukan lewat NVR): stream langsung dari IP
// kamera, biasanya rtsp biasa (tanpa TLS) di port 554, dan selalu channel 1.
function buildIpPtzAddress(ip) {
  let host = ip.replace(/^rtsps?:\/\//, '').replace(/\/.*$/, '')
  if (!host) throw new Error('invalid ip')
  if (!/:\d+$/.test(host)) host = `${host}:554`
  return `rtsp://${host}/cam/realmonitor?subtype=0&unicast=true&proto=Onvif`
}

const CAM_TYPES = [
  { id: 'nvr',   label: '📼 Channel NVR' },
  { id: 'ipptz', label: '🕹 Kamera IP PTZ' },
]

export default function AddChannelModal({ onAdd, onClose }) {
  const [camType, setCamType]   = useState('nvr')
  const [name,    setName]      = useState('')
  const [rtspUrl, setRtspUrl]   = useState('')
  const [ipAddress, setIpAddress] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [channel, setChannel]   = useState('5')
  const [error,   setError]     = useState('')
  const [loading, setLoading]   = useState(false)
  const submitting = useRef(false)
  const rtspTouched = useRef(false)

  // Default form diambil dari konfigurasi NVR di backend — tidak ada
  // host/kredensial hardcode di frontend.
  useEffect(() => {
    let alive = true
    fetchNvrConfig()
      .then(cfg => {
        if (!alive) return
        if (cfg.stream_user) setUsername(u => u || cfg.stream_user)
        if (cfg.stream_pass) setPassword(p => p || cfg.stream_pass)
        if (cfg.host && !rtspTouched.current) {
          setRtspUrl(v => v || `rtsps://${cfg.host}:554/cam/realmonitor?subtype=0&unicast=true&proto=Onvif&tls=true`)
        }
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const submit = async () => {
    if (submitting.current) return
    const n = name.trim()
    const user = username.trim()

    if (!n) { setError('Nama tidak boleh kosong'); return }
    if (!user) { setError('Username tidak boleh kosong'); return }
    if (!password) { setError('Password tidak boleh kosong'); return }

    let address
    let ch
    let ptzSupported

    if (camType === 'ipptz') {
      const ip = ipAddress.trim()
      if (!ip) { setError('IP kamera tidak boleh kosong'); return }
      try {
        address = buildIpPtzAddress(ip)
      } catch {
        setError('IP kamera tidak valid'); return
      }
      // Kamera IP PTZ hanya punya 1 channel — tidak perlu input
      ch = 1
      ptzSupported = true
    } else {
      const rtsp = rtspUrl.trim()
      const chRaw = channel.trim()
      if (!rtsp) { setError('Alamat RTSP tidak boleh kosong'); return }
      if (!/^\d+$/.test(chRaw) || Number(chRaw) <= 0) { setError('Channel harus angka lebih dari 0'); return }
      address = rtsp
      ch = Number(chRaw)
      ptzSupported = false
    }

    submitting.current = true
    setLoading(true)
    try {
      await onAdd(n, buildRtspUrl(address, user, password), ch, ptzSupported)
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

        <label>Jenis Kamera</label>
        <div style={{ display: 'flex', gap: 6, marginBottom: 4 }}>
          {CAM_TYPES.map(t => (
            <button
              key={t.id}
              type="button"
              className={`btn ${camType === t.id ? 'btn-primary' : 'btn-ghost'}`}
              style={{ flex: 1, fontSize: '.8rem', padding: '7px 8px' }}
              onClick={() => { setCamType(t.id); setError('') }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <label>Nama Channel</label>
        <input
          autoFocus
          placeholder={camType === 'ipptz' ? 'mis. PTZ Halaman' : 'mis. Camera Belakang'}
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
        />

        {camType === 'ipptz' ? (
          <>
            <label>IP Kamera</label>
            <input
              placeholder="mis. 10.10.30.20"
              value={ipAddress}
              onChange={e => { setIpAddress(e.target.value); setError('') }}
            />
          </>
        ) : (
          <>
            <label>Alamat RTSP</label>
            <input
              placeholder={RTSP_PLACEHOLDER}
              value={rtspUrl}
              onChange={e => { rtspTouched.current = true; setRtspUrl(e.target.value); setError('') }}
            />
          </>
        )}

        <label>Username</label>
        <input
          placeholder={camType === 'ipptz' ? 'username kamera' : 'username NVR'}
          value={username}
          onChange={e => { setUsername(e.target.value); setError('') }}
        />

        <label>Password</label>
        <input
          type="password"
          placeholder={camType === 'ipptz' ? 'password kamera' : 'password NVR'}
          value={password}
          onChange={e => { setPassword(e.target.value); setError('') }}
        />

        {camType === 'nvr' && (
          <>
            <label>Nomor Channel</label>
            <input
              inputMode="numeric"
              placeholder="mis. 5"
              value={channel}
              onChange={e => { setChannel(e.target.value); setError('') }}
            />
          </>
        )}

        <div className="modal-hint">
          {camType === 'ipptz' ? (
            <>Cukup isi IP kamera — kamera IP PTZ hanya punya 1 channel, jadi
            nomor channel tidak perlu. Kontrol PTZ otomatis aktif dan perintah
            pan/tilt/zoom dikirim langsung ke IP kamera.</>
          ) : (
            <>Isi alamat RTSP dasar tanpa username, password, dan tanpa `channel=`.
            Kamu juga bisa pakai placeholder <code style={{ color: 'var(--accent)' }}>{'{channel}'}</code>.
            Sistem akan membuat path HLS custom otomatis di MediaMTX.</>
          )}
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
