import React, { useEffect, useRef, useState } from 'react'
import { fetchNvrConfig } from '../../services/api'

const DEFAULT_USERNAME = 'dashboard'
const DEFAULT_PASSWORD = 'd4$hb0ard-dlt'

function buildRtspUrl({ ip, port, channel, subtype, useTls, username, password }) {
  const scheme = useTls ? 'rtsps' : 'rtsp'
  const portPart = (port && port !== '554') ? `:${port}` : ':554'
  const params = new URLSearchParams()
  params.set('channel', channel || '1')
  params.set('subtype', subtype || '0')
  params.set('unicast', 'true')
  params.set('proto', 'Onvif')
  if (useTls) params.set('tls', 'true')
  return `${scheme}://${username}:${password}@${ip}${portPart}/cam/realmonitor?${params.toString()}`
}

function maskPassword(url) {
  return url.replace(/:([^@:]+)@/, ':••••••@')
}

export default function AddChannelModal({ onAdd, onClose }) {
  const [name,     setName]     = useState('')
  const [ip,       setIp]       = useState('')
  const [port,     setPort]     = useState('554')
  const [channel,  setChannel]  = useState('')
  const [isIpCam,  setIsIpCam]  = useState(false)
  const [subtype,  setSubtype]  = useState('0')
  const [useTls,   setUseTls]   = useState(true)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPass, setShowPass] = useState(false)
  const [error,    setError]    = useState('')
  const [loading,  setLoading]  = useState(false)
  const submitting   = useRef(false)
  const defaultCreds = useRef({ user: DEFAULT_USERNAME, pass: DEFAULT_PASSWORD })

  useEffect(() => {
    let alive = true
    fetchNvrConfig()
      .then(cfg => {
        if (!alive) return
        if (cfg.stream_user) defaultCreds.current.user = cfg.stream_user
        if (cfg.stream_pass) defaultCreds.current.pass = cfg.stream_pass
        if (cfg.host) setIp(cfg.host)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  const effChannel  = isIpCam ? '1' : (channel.trim() || '')
  const effUsername = username.trim() || defaultCreds.current.user
  const effPassword = password        || defaultCreds.current.pass

  const rtspPreview = ip.trim()
    ? buildRtspUrl({ ip: ip.trim(), port, channel: effChannel || '1', subtype, useTls, username: effUsername, password: effPassword })
    : ''

  const submit = async () => {
    if (submitting.current) return
    const n = name.trim()
    if (!n)         { setError('Nama tidak boleh kosong'); return }
    if (!ip.trim()) { setError('IP kamera tidak boleh kosong'); return }
    if (!isIpCam && (!/^\d+$/.test(effChannel) || Number(effChannel) <= 0)) {
      setError('Nomor channel NVR harus diisi'); return
    }
    submitting.current = true
    setLoading(true)
    try {
      const finalUrl = buildRtspUrl({
        ip: ip.trim(), port, channel: effChannel, subtype,
        useTls, username: effUsername, password: effPassword,
      })
      await onAdd(n, finalUrl, Number(effChannel))
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      submitting.current = false
      setLoading(false)
    }
  }

  const row = { display: 'grid', gap: 8 }
  const inlineLabel = { display: 'block', fontSize: '.75rem', fontWeight: 600, color: 'var(--text-dim)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '.06em' }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" onKeyDown={e => e.key === 'Escape' && onClose()} style={{ maxWidth: 460 }}>
        <div className="modal-title">➕ Tambah Channel CCTV</div>

        {/* ── Nama ── */}
        <label>Nama Channel</label>
        <input
          autoFocus
          placeholder="mis. Pintu Depan"
          value={name}
          onChange={e => { setName(e.target.value); setError('') }}
        />

        {/* ── IP + Port ── */}
        <div style={{ ...row, gridTemplateColumns: '1fr 80px' }}>
          <div>
            <label>IP Kamera / NVR</label>
            <input
              placeholder="mis. 192.168.1.100"
              value={ip}
              onChange={e => { setIp(e.target.value); setError('') }}
            />
          </div>
          <div>
            <label>Port</label>
            <input
              placeholder="554"
              value={port}
              onChange={e => setPort(e.target.value)}
            />
          </div>
        </div>

        {/* ── Tipe kamera ── */}
        <label className="check-label" style={{ marginTop: 14 }}>
          <input
            type="checkbox"
            checked={isIpCam}
            onChange={e => { setIsIpCam(e.target.checked); setError('') }}
          />
          <span>
            Kamera IP langsung{' '}
            <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
              (DH-P5AE-PV, IPC — channel otomatis = 1)
            </span>
          </span>
        </label>

        {/* ── Channel NVR ── */}
        {!isIpCam && (
          <>
            <label>Nomor Channel NVR</label>
            <input
              inputMode="numeric"
              placeholder="mis. 5"
              value={channel}
              onChange={e => { setChannel(e.target.value); setError('') }}
            />
          </>
        )}

        {/* ── Quality + TLS ── */}
        <div style={{ ...row, gridTemplateColumns: '1fr 1fr', marginTop: 14 }}>
          <div>
            <span style={inlineLabel}>Kualitas Stream</span>
            <div style={{ display: 'flex', gap: 16 }}>
              <label className="check-label">
                <input type="radio" name="subtype" checked={subtype === '0'} onChange={() => setSubtype('0')} />
                Main (HD)
              </label>
              <label className="check-label">
                <input type="radio" name="subtype" checked={subtype === '1'} onChange={() => setSubtype('1')} />
                Sub (SD)
              </label>
            </div>
          </div>
          <div>
            <span style={inlineLabel}>Enkripsi</span>
            <label className="check-label">
              <input type="checkbox" checked={useTls} onChange={e => setUseTls(e.target.checked)} />
              TLS (rtsps://)
            </label>
          </div>
        </div>

        {/* ── Credentials ── */}
        <div style={{ ...row, gridTemplateColumns: '1fr 1fr' }}>
          <div>
            <label>
              Username{' '}
              <span style={{ color: 'var(--text-muted)', fontWeight: 400, textTransform: 'none', letterSpacing: 0, fontSize: '.72rem' }}>
                (kosong = default)
              </span>
            </label>
            <input
              placeholder={DEFAULT_USERNAME}
              value={username}
              onChange={e => { setUsername(e.target.value); setError('') }}
            />
          </div>
          <div>
            <label>
              Password{' '}
              <span style={{ color: 'var(--text-muted)', fontWeight: 400, textTransform: 'none', letterSpacing: 0, fontSize: '.72rem' }}>
                (kosong = default)
              </span>
            </label>
            <div style={{ position: 'relative' }}>
              <input
                type={showPass ? 'text' : 'password'}
                placeholder="••••••••••••"
                value={password}
                onChange={e => { setPassword(e.target.value); setError('') }}
                style={{ paddingRight: 32 }}
              />
              <span
                onClick={() => setShowPass(v => !v)}
                style={{ position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)', cursor: 'pointer', fontSize: '.8rem', color: 'var(--text-muted)' }}
              >
                {showPass ? '🙈' : '👁'}
              </span>
            </div>
          </div>
        </div>

        {/* ── RTSP URL Preview ── */}
        {rtspPreview && (
          <div style={{
            background: 'var(--bg)',
            border: '1px solid var(--border2)',
            borderRadius: 'var(--r-sm)',
            padding: '8px 10px',
            marginTop: 12,
            wordBreak: 'break-all',
            fontSize: '.72rem',
            fontFamily: 'monospace',
            color: 'var(--text-muted)',
            lineHeight: 1.6,
          }}>
            <span style={{ fontFamily: 'sans-serif', textTransform: 'uppercase', fontSize: '.65rem', letterSpacing: '.05em', color: 'var(--text-dim)' }}>
              Preview RTSP URL
            </span>
            <br />
            {maskPassword(rtspPreview)}
          </div>
        )}

        {error && (
          <div style={{ color: 'var(--red)', fontSize: '.78rem', marginTop: 8 }}>⚠ {error}</div>
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

