import React, { useEffect, useState } from 'react'
import { fetchSirenConfig, postSirenConfig } from '../../services/api'

export default function SirenConfig() {
  const [host, setHost]       = useState('')
  const [user, setUser]       = useState('')
  const [pass, setPass]       = useState('')
  const [enabled, setEnabled] = useState(true)
  const [showPass, setShowPass] = useState(false)
  const [saving, setSaving]   = useState(false)
  const [msg, setMsg]         = useState(null)

  useEffect(() => {
    fetchSirenConfig()
      .then(cfg => {
        setHost(cfg.host || '')
        setUser(cfg.user || '')
        setPass(cfg.pass || '')
        setEnabled(cfg.enabled !== false)
      })
      .catch(() => setMsg({ ok: false, text: 'Gagal memuat konfigurasi siren' }))
  }, [])

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setMsg(null)
    try {
      await postSirenConfig({ host, user, pass, enabled })
      setMsg({ ok: true, text: 'Konfigurasi siren tersimpan.' })
    } catch (err) {
      setMsg({ ok: false, text: err.message || 'Gagal menyimpan' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="siren-config">
      <form className="nvr-cred-form" onSubmit={handleSave}>
        <div className="nvr-cred-group-title">Konfigurasi Siren / Speaker Kamera</div>

        <div className="nvr-cred-field">
          <label>IP / Host Kamera Siren</label>
          <input
            type="text"
            value={host}
            onChange={e => setHost(e.target.value)}
            placeholder="10.10.30.5"
          />
        </div>

        <div className="nvr-cred-field">
          <label>Username</label>
          <input
            type="text"
            value={user}
            onChange={e => setUser(e.target.value)}
            placeholder="admin"
            autoComplete="username"
          />
        </div>

        <div className="nvr-cred-field">
          <label>Password</label>
          <div className="nvr-pass-wrap">
            <input
              type={showPass ? 'text' : 'password'}
              value={pass}
              onChange={e => setPass(e.target.value)}
              autoComplete="current-password"
            />
            <button type="button" className="btn btn-ghost nvr-toggle-pass"
              onClick={() => setShowPass(v => !v)}>
              {showPass ? '🙈' : '👁'}
            </button>
          </div>
        </div>

        <div className="nvr-cred-field" style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <label style={{ flex: 'none', margin: 0 }}>Siren Aktif</label>
          <button
            type="button"
            className={`zone-toggle-btn ${enabled ? 'tog-on' : 'tog-off'}`}
            onClick={() => setEnabled(v => !v)}
          >
            {enabled ? 'Aktif' : 'Nonaktif'}
          </button>
        </div>

        {msg && (
          <div className={`nvr-msg ${msg.ok ? 'ok' : 'err'}`}>{msg.text}</div>
        )}

        <button type="submit" className="btn" disabled={saving}>
          {saving ? 'Menyimpan…' : '💾 Simpan Konfigurasi Siren'}
        </button>
      </form>

      <div className="nvr-cred-note">
        Masukkan IP kamera yang memiliki speaker (contoh: DH-P5AE-PV).
        Siren akan otomatis diaktifkan saat alarm trigger sesuai pengaturan zona.
      </div>
    </div>
  )
}
