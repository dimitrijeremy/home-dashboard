import React, { useEffect, useState } from 'react'
import { fetchCameras, fetchNvrConfig, postNvrConfig, fetchNvrInfo, fetchAiConfig, postAiConfig } from '../services/api'
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
  const [nvrHost, setNvrHost]     = useState('')
  const [nvrPort, setNvrPort]     = useState('80')
  const [streamUser, setStreamUser] = useState('')
  const [streamPass, setStreamPass] = useState('')
  const [nvrUser, setNvrUser]     = useState('')
  const [nvrPass, setNvrPass]     = useState('')
  const [streamQuality, setStreamQuality] = useState('source')
  const [showPass, setShowPass]   = useState(false)
  const [nvrSaving, setNvrSaving] = useState(false)
  const [nvrMsg, setNvrMsg]       = useState(null)  // { ok, text }
  const [nvrInfo, setNvrInfo]     = useState(null)
  const [nvrInfoLoading, setNvrInfoLoading] = useState(false)

  // AI global toggle (master switch — beda dari toggle ai_enabled per-kamera)
  const [aiEnabled, setAiEnabled] = useState(true)
  const [aiSaving,  setAiSaving]  = useState(false)

  const formatBytes = (value) => {
    if (!value) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB', 'TB']
    let size = value
    let idx = 0
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024
      idx += 1
    }
    return `${size.toFixed(idx < 2 ? 0 : 1)} ${units[idx]}`
  }

  useEffect(() => {
    fetchCameras()
      .then(list => {
        setCams(list)
        if (list.length > 0) setSelectedCam(list[0])
      })
      .catch(() => {})
    fetchAiConfig().then(cfg => setAiEnabled(cfg.enabled)).catch(() => {})
  }, [])

  async function handleAiToggle() {
    const next = !aiEnabled
    setAiSaving(true)
    setAiEnabled(next)   // optimistic
    try {
      await postAiConfig(next)
    } catch {
      setAiEnabled(!next)   // revert on failure
    } finally {
      setAiSaving(false)
    }
  }

  useEffect(() => {
    if (tab === 'nvr') {
      setNvrInfoLoading(true)
      fetchNvrConfig()
        .then(cfg => {
          setNvrHost(cfg.host || '')
          setNvrPort(String(cfg.http_port || 80))
          setStreamUser(cfg.stream_user || '')
          setStreamPass(cfg.stream_pass || '')
          setNvrUser(cfg.event_user || '')
          setNvrPass(cfg.event_pass || '')
          setStreamQuality(cfg.stream_quality || 'source')
        })
        .catch(() => setNvrMsg({ ok: false, text: 'Gagal memuat konfigurasi NVR' }))

      fetchNvrInfo()
        .then(setNvrInfo)
        .catch(err => setNvrMsg(prev => prev || { ok: false, text: err.message || 'Gagal memuat info NVR' }))
        .finally(() => setNvrInfoLoading(false))
    }
  }, [tab])

  async function handleNvrSave(e) {
    e.preventDefault()
    setNvrSaving(true)
    setNvrMsg(null)
    try {
      await postNvrConfig({
        host: nvrHost,
        http_port: Number(nvrPort) || 80,
        stream_user: streamUser,
        stream_pass: streamPass,
        event_user: nvrUser,
        event_pass: nvrPass,
        stream_quality: streamQuality,
      })
      setNvrMsg({ ok: true, text: 'Tersimpan. Restart stream dari dashboard agar kualitas/kredensial baru dipakai; event worker saat reconnect.' })
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

      <div className={`ai-master-toggle ${aiEnabled ? '' : 'off'}`}>
        <div>
          <div className="ai-master-toggle-title">
            {aiEnabled ? '🧠 AI Detection: Aktif' : '📹 AI Detection: Nonaktif — fokus streaming'}
          </div>
          <div className="ai-master-toggle-hint">
            {aiEnabled
              ? 'Zona, wajah, dan riwayat deteksi berjalan normal. Matikan untuk hemat CPU/RAM kalau server sedang berat.'
              : 'Semua deteksi AI (zona, wajah) berhenti total di semua kamera — streaming CCTV tetap jalan seperti biasa. Toggle AI per-kamera diabaikan sementara.'}
          </div>
        </div>
        <button
          type="button"
          className={`ai-master-switch ${aiEnabled ? 'on' : ''}`}
          onClick={handleAiToggle}
          disabled={aiSaving}
          aria-pressed={aiEnabled}
        >
          <span className="ai-master-switch-knob" />
        </button>
      </div>

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

            <div className="nvr-info-block">
              <div className="nvr-cred-group-title">Info dari API NVR</div>
              {nvrInfoLoading && <div className="nvr-info-empty">Memuat info NVR…</div>}
              {!nvrInfoLoading && nvrInfo && (
                <>
                  <div className="nvr-info-grid">
                    <div className="nvr-info-card">
                      <div className="nvr-info-title">Perangkat</div>
                      <div className="nvr-info-kv"><span>Model</span><strong>{nvrInfo.device.model || '—'}</strong></div>
                      <div className="nvr-info-kv"><span>Serial</span><strong>{nvrInfo.device.serial_number || '—'}</strong></div>
                      <div className="nvr-info-kv"><span>Processor</span><strong>{nvrInfo.device.processor || '—'}</strong></div>
                      <div className="nvr-info-kv"><span>Host</span><strong>{`${nvrInfo.host}:${nvrInfo.http_port}`}</strong></div>
                    </div>

                    <div className="nvr-info-card">
                      <div className="nvr-info-title">Event Stream</div>
                      <div className="nvr-info-kv"><span>Status</span><strong className={nvrInfo.events.connected ? 'nvr-ok' : 'nvr-bad'}>{nvrInfo.events.connected ? 'Tersambung' : 'Terputus'}</strong></div>
                      <div className="nvr-info-kv"><span>Last Event</span><strong>{nvrInfo.events.last_event || '—'}</strong></div>
                      <div className="nvr-info-kv"><span>Error</span><strong>{nvrInfo.events.error || '—'}</strong></div>
                    </div>

                    <div className="nvr-info-card">
                      <div className="nvr-info-title">Storage</div>
                      <div className="nvr-info-kv"><span>Status</span><strong>{nvrInfo.storage.state || '—'}</strong></div>
                      <div className="nvr-info-kv"><span>Health Flag</span><strong>{nvrInfo.storage.health_flag || '—'}</strong></div>
                      <div className="nvr-disk-list">
                        {nvrInfo.storage.disks.map((disk) => (
                          <div key={disk.path} className="nvr-disk-row">
                            <div>
                              <strong>{disk.path}</strong>
                              <span>{disk.type || 'ReadWrite'}</span>
                            </div>
                            <div>
                              <strong>{formatBytes(disk.used_bytes)} / {formatBytes(disk.total_bytes)}</strong>
                              <span className={disk.is_error ? 'nvr-bad' : ''}>{disk.usage_percent != null ? `${disk.usage_percent}% terpakai` : '—'}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>

                  <div className="nvr-info-card">
                    <div className="nvr-info-title">Nama Channel dari NVR</div>
                    <div className="nvr-channel-grid">
                      {nvrInfo.channels.map((channel) => (
                        <div key={channel.index} className="nvr-channel-chip">
                          <span>{`Ch ${channel.index}`}</span>
                          <strong>{channel.name}</strong>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>

            <form className="nvr-cred-form" onSubmit={handleNvrSave}>
              <div className="nvr-cred-group-title">Alamat NVR</div>
              <div className="nvr-cred-field">
                <label>Host / IP NVR</label>
                <input
                  type="text"
                  value={nvrHost}
                  onChange={e => setNvrHost(e.target.value)}
                  placeholder="mis. 10.10.30.2"
                  required
                />
              </div>
              <div className="nvr-cred-field">
                <label>Port HTTP</label>
                <input
                  type="text"
                  inputMode="numeric"
                  value={nvrPort}
                  onChange={e => setNvrPort(e.target.value)}
                  placeholder="80"
                  required
                />
              </div>

              <div className="nvr-cred-group-title">Kualitas Stream</div>
              <div className="nvr-cred-field">
                <label>Resolusi / Sumber Stream</label>
                <select
                  className="cam-selector-select"
                  value={streamQuality}
                  onChange={e => setStreamQuality(e.target.value)}
                  style={{ width: '100%' }}
                >
                  <option value="source">Asli — main stream tanpa scale</option>
                  <option value="720">720p — main stream di-scale (hemat sedang)</option>
                  <option value="480">480p — main stream di-scale (hemat besar)</option>
                  <option value="sub">Substream NVR — subtype 1 (paling hemat)</option>
                </select>
                <small style={{ fontSize: '.72rem', color: 'var(--text-muted)' }}>
                  Berlaku untuk semua channel setelah stream direstart (tombol ↺ di dashboard).
                </small>
              </div>

              <div className="nvr-cred-group-title">Stream CCTV Channel Built-in</div>
              <div className="nvr-cred-field">
                <label>Username Stream</label>
                <input
                  type="text"
                  value={streamUser}
                  onChange={e => setStreamUser(e.target.value)}
                  placeholder="username NVR"
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
                  placeholder="username NVR"
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
