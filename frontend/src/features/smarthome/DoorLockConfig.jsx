import React, { useEffect, useState } from 'react'
import { fetchDoorlockConfig, postDoorlockConfig } from '../../services/api'

const REGIONS = [
  { value: 'us', label: 'Amerika (US)' },
  { value: 'eu', label: 'Eropa (EU)' },
  { value: 'cn', label: 'China (CN)' },
  { value: 'in', label: 'India (IN)' },
]

export default function DoorLockConfig() {
  const [accessId, setAccessId]       = useState('')
  const [accessSecret, setAccessSecret] = useState('')
  const [deviceId, setDeviceId]       = useState('')
  const [region, setRegion]           = useState('us')
  const [uid, setUid]                 = useState('')
  const [showSecret, setShowSecret]   = useState(false)
  const [saving, setSaving]           = useState(false)
  const [msg, setMsg]                 = useState(null)
  const [connected, setConnected]     = useState(false)

  useEffect(() => {
    fetchDoorlockConfig()
      .then(cfg => {
        setAccessId(cfg.access_id || '')
        setAccessSecret(cfg.access_secret || '')
        setDeviceId(cfg.device_id || '')
        setRegion(cfg.region || 'us')
        setUid(cfg.uid || '')
        setConnected(cfg.connected || false)
        if (cfg.error) {
          setMsg({ ok: false, text: cfg.error })
        }
      })
      .catch(() => setMsg({ ok: false, text: 'Gagal memuat konfigurasi door lock' }))
  }, [])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setMsg(null)
    try {
      const result = await postDoorlockConfig({
        access_id: accessId,
        access_secret: accessSecret,
        device_id: deviceId,
        region,
        uid,
      })
      setConnected(result.connected)
      if (result.connected) {
        setMsg({ ok: true, text: 'Terhubung ke Tuya Cloud! Door lock siap digunakan.' })
      } else {
        setMsg({ ok: false, text: result.error || 'Gagal terhubung ke Tuya Cloud' })
      }
    } catch (err) {
      setMsg({ ok: false, text: err.message || 'Gagal menyimpan' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="doorlock-config">
      <form className="nvr-cred-form" onSubmit={handleSave}>
        <div className="nvr-cred-group-title">
          🔐 Konfigurasi Smart Door Lock (Paloma DLP6202)
        </div>

        <div className="nvr-cred-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <label style={{ flex: 'none', margin: 0 }}>Status</label>
          <span className={`doorlock-status ${connected ? 'online' : 'offline'}`}>
            {connected ? '● Terhubung' : '○ Tidak terhubung'}
          </span>
        </div>

        <div className="nvr-cred-field">
          <label>Tuya Region</label>
          <select value={region} onChange={e => setRegion(e.target.value)}>
            {REGIONS.map(r => (
              <option key={r.value} value={r.value}>{r.label}</option>
            ))}
          </select>
        </div>

        <div className="nvr-cred-field">
          <label>Access ID (Client ID)</label>
          <input
            type="text"
            value={accessId}
            onChange={e => setAccessId(e.target.value)}
            placeholder="Dari Tuya IoT Platform"
          />
        </div>

        <div className="nvr-cred-field">
          <label>Access Secret (Client Secret)</label>
          <div className="nvr-pass-wrap">
            <input
              type={showSecret ? 'text' : 'password'}
              value={accessSecret}
              onChange={e => setAccessSecret(e.target.value)}
              placeholder="Dari Tuya IoT Platform"
              autoComplete="off"
            />
            <button type="button" className="btn btn-ghost nvr-toggle-pass"
              onClick={() => setShowSecret(v => !v)}>
              {showSecret ? '🙈' : '👁'}
            </button>
          </div>
        </div>

        <div className="nvr-cred-field">
          <label>Device ID</label>
          <input
            type="text"
            value={deviceId}
            onChange={e => setDeviceId(e.target.value)}
            placeholder="ID perangkat di Tuya Cloud"
          />
        </div>

        <div className="nvr-cred-field">
          <label>User UID (opsional)</label>
          <input
            type="text"
            value={uid}
            onChange={e => setUid(e.target.value)}
            placeholder="UID akun Tuya/Smart Life yang terhubung"
          />
        </div>

        {msg && (
          <div className={`nvr-msg ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>
        )}

        <button type="submit" className="btn" disabled={saving}>
          {saving ? 'Menyimpan…' : '💾 Simpan & Hubungkan'}
        </button>
      </form>

      <div className="nvr-cred-note">
        <strong>Cara mendapatkan kredensial Tuya:</strong>
        <ol style={{ margin: '8px 0', paddingLeft: 20 }}>
          <li>Daftar di <a href="https://iot.tuya.com" target="_blank" rel="noreferrer">iot.tuya.com</a></li>
          <li>Buat Cloud Project → pilih region yang sesuai</li>
          <li>Link akun Smart Life / Tuya Smart Anda ke project</li>
          <li>Catat Access ID, Access Secret, dan Device ID</li>
          <li>Pastikan API permissions: IoT Core, Smart Lock, IR Control, IPC</li>
        </ol>
      </div>
    </div>
  )
}
