import React, { useEffect, useState } from 'react'
import { fetchZoneAlarmSettings, postZoneAlarmSettings, fetchSounds, uploadSound, deleteSound } from '../../services/api'

export default function ZoneAlarmSettings({ zone }) {
  const [settings, setSettings] = useState(null)
  const [sounds, setSounds]     = useState([])
  const [saving, setSaving]     = useState(false)
  const [msg, setMsg]           = useState(null)
  const [uploading, setUploading] = useState(false)

  useEffect(() => {
    if (!zone?.id) return
    fetchZoneAlarmSettings(zone.id)
      .then(setSettings)
      .catch(() => setSettings({
        trigger_on_home: false,
        trigger_on_away: true,
        sound_file: 'alarm',
        chime_on_home: true,
      }))
    fetchSounds().then(setSounds).catch(() => {})
  }, [zone?.id])

  if (!settings) return null

  async function handleSave() {
    setSaving(true)
    setMsg(null)
    try {
      const updated = await postZoneAlarmSettings(zone.id, settings)
      setSettings(updated)
      setMsg({ ok: true, text: 'Tersimpan' })
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    } finally {
      setSaving(false)
    }
  }

  async function handleUpload(e) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    try {
      const result = await uploadSound(file)
      setSounds(prev => [...prev, result])
      setMsg({ ok: true, text: `File "${result.name}" terupload` })
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  async function handleDeleteSound(filename) {
    if (!confirm(`Hapus file suara "${filename}"?`)) return
    try {
      await deleteSound(filename)
      setSounds(prev => prev.filter(s => s.filename !== filename))
    } catch (err) {
      setMsg({ ok: false, text: err.message })
    }
  }

  return (
    <div className="zone-alarm-settings">
      <div className="zone-alarm-title">⚙ Pengaturan Alarm — {zone.name}</div>

      <div className="zone-alarm-row">
        <label>
          <input
            type="checkbox"
            checked={settings.trigger_on_away}
            onChange={e => setSettings(s => ({ ...s, trigger_on_away: e.target.checked }))}
          />
          Trigger alarm saat <strong>Away</strong> (mode pergi)
        </label>
      </div>

      <div className="zone-alarm-row">
        <label>
          <input
            type="checkbox"
            checked={settings.trigger_on_home}
            onChange={e => setSettings(s => ({ ...s, trigger_on_home: e.target.checked }))}
          />
          Trigger alarm saat <strong>Home</strong> (mode di rumah)
        </label>
      </div>

      <div className="zone-alarm-row">
        <label>
          <input
            type="checkbox"
            checked={settings.chime_on_home}
            onChange={e => setSettings(s => ({ ...s, chime_on_home: e.target.checked }))}
          />
          Bunyikan <strong>chime</strong> saat Home (notifikasi masuk, bukan alarm)
        </label>
      </div>

      <div className="zone-alarm-row">
        <label>File Suara Alarm:</label>
        <select
          value={settings.sound_file}
          onChange={e => setSettings(s => ({ ...s, sound_file: e.target.value }))}
          className="zone-alarm-select"
        >
          {sounds.map(s => (
            <option key={s.filename} value={s.name}>{s.name}{s.builtin ? ' (bawaan)' : ''}</option>
          ))}
        </select>
      </div>

      <div className="zone-alarm-row">
        <label>Upload Suara Custom (.mp3/.wav/.ogg):</label>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="file"
            accept=".mp3,.wav,.ogg"
            onChange={handleUpload}
            disabled={uploading}
            style={{ fontSize: '.8rem' }}
          />
          {uploading && <span style={{ fontSize: '.75rem', color: 'var(--text-muted)' }}>Uploading…</span>}
        </div>
      </div>

      {sounds.filter(s => !s.builtin).length > 0 && (
        <div className="zone-alarm-sounds-list">
          <div style={{ fontSize: '.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Custom sounds:</div>
          {sounds.filter(s => !s.builtin).map(s => (
            <div key={s.filename} className="zone-alarm-sound-item">
              <span>🔊 {s.name}</span>
              <button
                className="btn-icon"
                onClick={() => handleDeleteSound(s.filename)}
                title="Hapus"
              >🗑</button>
            </div>
          ))}
        </div>
      )}

      {msg && (
        <div className={`nvr-msg ${msg.ok ? 'ok' : 'err'}`} style={{ marginTop: 8 }}>{msg.text}</div>
      )}

      <button className="btn" onClick={handleSave} disabled={saving} style={{ marginTop: 10 }}>
        {saving ? 'Menyimpan…' : '💾 Simpan Pengaturan Zona'}
      </button>
    </div>
  )
}
