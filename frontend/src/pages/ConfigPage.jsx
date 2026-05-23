import React, { useEffect, useState } from 'react'
import { fetchCameras, fetchNvrConfig, postNvrConfig } from '../services/api'
import ZoneEditor from '../features/detection/ZoneEditor'
import FaceManager from '../features/detection/FaceManager'
import EventHistory from '../features/detection/EventHistory'

const TABS = [
  { id: 'zones',   label: '📐 Zona Perimeter' },
  { id: 'faces',   label: '👤 Wajah Dikenal' },
  { id: 'history', label: '📋 Riwayat Deteksi' },
  { id: 'nvr',     label: '📡 Kredensial NVR' },
]

export default function ConfigPage({ onBack }) {
  const [tab, setTab]         = useState('zones')
  const [cams, setCams]       = useState([])
  const [selectedCam, setSelectedCam] = useState(null)

  // NVR config state
  const [streamUser, setStreamUser] = useState('')
  const [streamPass, setStreamPass] = useState('')
  const [nvrUser, setNvrUser]     = useState('')
  const [nvrPass, setNvrPass]     = useState('')
  const [showPass, setShowPass]   = useState(false)
  const [nvrSaving, setNvrSaving] = useState(false)
  const [nvrMsg, setNvrMsg]       = useState(null)  // { ok, text }

  useEffect(() => {
    fetchCameras()
      .then(list => {
        setCams(list)
        if (list.length > 0) setSelectedCam(list[0])
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (tab === 'nvr') {
      fetchNvrConfig()
        .then(cfg => {
          setStreamUser(cfg.stream_user || '')
          setStreamPass(cfg.stream_pass || '')
          setNvrUser(cfg.event_user || '')
          setNvrPass(cfg.event_pass || '')
        })
        .catch(() => setNvrMsg({ ok: false, text: 'Gagal memuat konfigurasi NVR' }))
    }
  }, [tab])

  async function handleNvrSave(e) {
    e.preventDefault()
    setNvrSaving(true)
    setNvrMsg(null)
    try {
      await postNvrConfig({
        stream_user: streamUser,
        stream_pass: streamPass,
        event_user: nvrUser,
        event_pass: nvrPass,
      })
      setNvrMsg({ ok: true, text: 'Tersimpan. Channel 1-4 akan memakai kredensial baru saat stream direstart; event worker saat reconnect.' })
    } catch (err) {
      setNvrMsg({ ok: false, text: err.message || 'Gagal menyimpan' })
    } finally {
      setNvrSaving(false)
    }
  }

  return (
    <div className="config-page">
      {/* Header */}
      <header className="config-header">
        <button className="btn btn-ghost config-back-btn" onClick={onBack}>
          ← Dashboard
        </button>
        <div className="config-page-title">
          <span>🛡</span> Konfigurasi AI Deteksi
        </div>
      </header>

      {/* Tabs */}
      <div className="config-tabs">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`config-tab-btn${tab === t.id ? ' active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="config-content">

        {tab === 'zones' && (
          <div className="config-section">
            <div className="config-section-intro">
              Gambar zona perimeter pada tiap kamera. Deteksi akan memicu event saat ada orang memasuki zona.
            </div>
            {cams.length === 0 ? (
              <p style={{ color: 'var(--text-muted)', fontSize: '.85rem' }}>Belum ada kamera terdaftar.</p>
            ) : (
              <>
                <div className="cam-selector-row">
                  <label className="cam-selector-label">Pilih Kamera:</label>
                  <select
                    className="cam-selector-select"
                    value={selectedCam?.id ?? ''}
                    onChange={e => setSelectedCam(cams.find(c => c.id === Number(e.target.value)))}
                  >
                    {cams.map(c => (
                      <option key={c.id} value={c.id}>{c.name}</option>
                    ))}
                  </select>
                </div>
                {selectedCam && <ZoneEditor key={selectedCam.id} camera={selectedCam} />}
              </>
            )}
          </div>
        )}

        {tab === 'faces' && (
          <div className="config-section">
            <div className="config-section-intro">
              Daftarkan wajah penghuni rumah. Sistem akan memberi tahu saat wajah dikenal atau asing terdeteksi.
            </div>
            <FaceManager />
          </div>
        )}

        {tab === 'history' && (
          <div className="config-section">
            <div className="config-section-intro">
              Riwayat semua event yang terdeteksi oleh sistem AI.
            </div>
            <EventHistory />
          </div>
        )}

        {tab === 'nvr' && (
          <div className="config-section">
            <div className="config-section-intro">
              Kredensial NVR untuk stream kamera channel 1-4 dan event deteksi.
              Setelah mengganti kredensial stream, restart stream dari dashboard agar MediaMTX membaca nilai terbaru.
            </div>
            <form className="nvr-cred-form" onSubmit={handleNvrSave}>
              <div className="nvr-cred-group-title">Stream CCTV Channel 1-4</div>
              <div className="nvr-cred-field">
                <label>Username Stream</label>
                <input
                  type="text"
                  value={streamUser}
                  onChange={e => setStreamUser(e.target.value)}
                  placeholder="dashboard2"
                  autoComplete="username"
                  required
                />
              </div>
              <div className="nvr-cred-field">
                <label>Password Stream</label>
                <div className="nvr-pass-wrap">
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={streamPass}
                    onChange={e => setStreamPass(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                </div>
              </div>

              <div className="nvr-cred-group-title">Event Deteksi NVR</div>
              <div className="nvr-cred-field">
                <label>Username Event</label>
                <input
                  type="text"
                  value={nvrUser}
                  onChange={e => setNvrUser(e.target.value)}
                  placeholder="dashboard2"
                  autoComplete="username"
                  required
                />
              </div>
              <div className="nvr-cred-field">
                <label>Password Event</label>
                <div className="nvr-pass-wrap">
                  <input
                    type={showPass ? 'text' : 'password'}
                    value={nvrPass}
                    onChange={e => setNvrPass(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                  <button type="button" className="btn btn-ghost nvr-toggle-pass"
                    onClick={() => setShowPass(v => !v)}>
                    {showPass ? '🙈' : '👁'}
                  </button>
                </div>
              </div>
              {nvrMsg && (
                <div className={`nvr-msg ${nvrMsg.ok ? 'ok' : 'err'}`}>
                  {nvrMsg.text}
                </div>
              )}
              <button type="submit" className="btn" disabled={nvrSaving}>
                {nvrSaving ? 'Menyimpan…' : '💾 Simpan Kredensial'}
              </button>
            </form>
            <div className="nvr-cred-note">
              Channel 1-4 mengambil credential dari config ini lewat backend. Tombol restart stream di dashboard cukup untuk memuat ulang credential.
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
