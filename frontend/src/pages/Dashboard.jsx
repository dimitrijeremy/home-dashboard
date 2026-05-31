import React, { useEffect, useRef, useState } from 'react'
import { fetchCameras, addCamera, deleteCamera, fetchStreamStatus, restartStream, restartAllStreams, fetchMode, postMode } from '../services/api'
import CCTVPlayer from '../features/cctv/CCTVPlayer'
import AddChannelModal from '../features/cctv/AddChannelModal'
import SmartControls from '../features/smarthome/SmartControls'
import DoorLockPanel from '../features/smarthome/DoorLockPanel'
import WeatherWidget from '../features/weather/WeatherWidget'
import PerformanceMonitor from '../features/smarthome/PerformanceMonitor'

// ── Perimeter Alert Toasts ─────────────────────────────────────────────────
const ALERT_CODES = new Set(['ZoneIntrusion', 'SmartMotionHuman', 'AlarmLocal', 'VideoMotion'])
const ALERT_TTL = 10000 // ms

function AlertToasts({ alerts, onDismiss }) {
  if (!alerts.length) return null
  return (
    <div style={{
      position: 'fixed', top: 72, right: 16, zIndex: 9999,
      display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 340,
    }}>
      {alerts.map(a => (
        <div key={a.id} style={{
          background: 'rgba(239,68,68,.92)', color: '#fff',
          borderRadius: 'var(--r-sm)', padding: '10px 14px',
          boxShadow: '0 4px 20px rgba(0,0,0,.4)',
          display: 'flex', gap: 10, alignItems: 'flex-start',
          backdropFilter: 'blur(4px)',
          animation: 'slideInRight .25s ease',
        }}>
          <span style={{ fontSize: 18 }}>🚨</span>
          <div style={{ flex: 1, lineHeight: 1.4 }}>
            <div style={{ fontWeight: 700, fontSize: '.88rem' }}>{a.code}</div>
            <div style={{ fontSize: '.8rem', opacity: .9 }}>
              {a.channel && `Ch${a.channel} · `}{a.time}
            </div>
          </div>
          <button
            onClick={() => onDismiss(a.id)}
            style={{ background: 'none', border: 'none', color: '#fff', cursor: 'pointer', fontSize: 14, padding: 0 }}
          >✕</button>
        </div>
      ))}
    </div>
  )
}

function Clock() {
  const [time, setTime] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const days = ['Minggu','Senin','Selasa','Rabu','Kamis','Jumat','Sabtu']
  return (
    <span className="header-clock">
      {days[time.getDay()]}, {time.toLocaleDateString('id-ID',{day:'2-digit',month:'short',year:'numeric'})}
      {' · '}
      {time.toLocaleTimeString('id-ID',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}
    </span>
  )
}

function StreamStatusPanel({ cams, open, onToggle }) {
  const [statuses, setStatuses]       = useState({})   // { [id]: 'online'|'offline'|'checking' }
  const [lastChecked, setLastChecked] = useState(null)
  const [restarting, setRestarting]   = useState({})   // { [id]: bool }
  const [restartingAll, setRestartingAll] = useState(false)

  const refresh = async () => {
    if (cams.length === 0) return
    const checking = {}
    cams.forEach(c => { checking[c.id] = 'checking' })
    setStatuses(checking)
    try {
      const data = await fetchStreamStatus()
      const next = {}
      data.forEach(s => { next[s.id] = s.online ? 'online' : 'offline' })
      setStatuses(next)
      setLastChecked(new Date())
    } catch {
      const err = {}
      cams.forEach(c => { err[c.id] = 'error' })
      setStatuses(err)
    }
  }

  const pollStatus = (targetIds, maxWaitMs = 35000, intervalMs = 3000) => {
    const deadline = Date.now() + maxWaitMs
    const tick = async () => {
      try {
        const data = await fetchStreamStatus()
        const next = {}
        data.forEach(s => { next[s.id] = s.online ? 'online' : 'offline' })
        setStatuses(next)
        setLastChecked(new Date())
        const allDone = targetIds.every(id => next[id] === 'online' || next[id] === undefined)
        if (!allDone && Date.now() < deadline) {
          setTimeout(tick, intervalMs)
          return
        }
      } catch { /* ignore, will retry */ }
      // Done (timeout or all online)
      setRestarting({})
      setRestartingAll(false)
    }
    setTimeout(tick, 5000)   // first check after 5s (let mediamtx kill+restart)
  }

  const restart = async (camId) => {
    setRestarting(prev => ({ ...prev, [camId]: true }))
    try { await restartStream(camId) } catch { /* ignore */ }
    pollStatus([camId])
  }

  const restartAll = async () => {
    setRestartingAll(true)
    const checking = {}
    // Mark all as restarting
    Object.keys(statuses).forEach(id => { checking[Number(id)] = true })
    setRestarting(checking)
    try { await restartAllStreams() } catch { /* ignore */ }
    pollStatus(Object.keys(statuses).map(Number))
  }

  useEffect(() => {
    if (open && Object.keys(statuses).length === 0) refresh()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const onlineCount  = Object.values(statuses).filter(s => s === 'online').length
  const hasStatuses  = Object.keys(statuses).length > 0
  const anyChecking  = Object.values(statuses).some(s => s === 'checking')
  const anyBusy      = anyChecking || restartingAll || Object.values(restarting).some(Boolean)

  const badgeLabel = (st, isRestarting) => {
    if (isRestarting) return 'Restarting…'
    if (st === 'online')   return '● Online'
    if (st === 'offline')  return '● Offline'
    if (st === 'checking') return 'Cek…'
    return '–'
  }
  const badgeClass = (st, isRestarting) => {
    if (isRestarting)      return 'stream-status-badge badge-checking'
    if (st === 'online')   return 'stream-status-badge badge-online'
    if (st === 'offline')  return 'stream-status-badge badge-offline'
    return 'stream-status-badge badge-checking'
  }

  return (
    <div className="stream-status-wrap">
      <button
        className="btn btn-ghost"
        style={{ fontSize: '.75rem' }}
        onClick={onToggle}
      >
        📡 {hasStatuses ? `${onlineCount}/${cams.length}` : 'Status'} {open ? '▲' : '▼'}
      </button>

      {open && (
        <div className="stream-status-panel">
          {cams.map(cam => {
            const st  = statuses[cam.id]
            const isR = restarting[cam.id]
            return (
              <div key={cam.id} className="stream-status-row">
                <span className="stream-status-name">{cam.name}</span>
                <span className={badgeClass(st, isR)}>{badgeLabel(st, isR)}</span>
                <button
                  className="btn-icon"
                  title="Restart stream"
                  disabled={anyBusy}
                  onClick={() => restart(cam.id)}
                  style={{ opacity: isR ? .4 : 1 }}
                >↺</button>
              </div>
            )
          })}
          <div className="stream-status-footer">
            <span className="stream-status-time">
              {lastChecked
                ? `Cek: ${lastChecked.toLocaleTimeString('id-ID')}`
                : 'Belum dicek'}
            </span>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                className="btn btn-ghost"
                style={{ fontSize: '.72rem', padding: '4px 10px' }}
                disabled={anyBusy}
                onClick={restartAll}
              >
                {restartingAll ? 'Restarting…' : '↺ Restart Semua'}
              </button>
              <button
                className="btn btn-ghost"
                style={{ fontSize: '.72rem', padding: '4px 10px' }}
                disabled={anyBusy}
                onClick={refresh}
              >
                ↻ Refresh
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// View modes: cols = grid columns for videos
const VIEW_MODES = [
  { id: 'grid2', icon: '⊞', label: '2 kolom',  cols: 2 },
  { id: 'grid3', icon: '⊟', label: '3 kolom',  cols: 3 },
  { id: 'grid1', icon: '▬', label: 'Full',      cols: 1 },
]

export default function Dashboard({ onConfig }) {
  const [cams,        setCams]        = useState([])
  const [error,       setError]       = useState(null)
  const [showModal,   setShowModal]   = useState(false)
  const [showStatus,  setShowStatus]  = useState(false)
  const [viewMode,    setViewMode]    = useState('grid2')
  const [mode,        setMode]        = useState('home')
  const [modeLoading, setModeLoading] = useState(false)
  const [alerts,      setAlerts]      = useState([])
  const alertTimers = useRef({})

  const dismissAlert = (id) => {
    clearTimeout(alertTimers.current[id])
    delete alertTimers.current[id]
    setAlerts(prev => prev.filter(a => a.id !== id))
  }

  // Subscribe to NVR event SSE for real-time perimeter alerts
  useEffect(() => {
    let es
    const connect = () => {
      es = new EventSource('/api/nvr-events/stream')
      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data)
          if (ALERT_CODES.has(event.code) && event.action === 'Start') {
            const id = `${Date.now()}-${Math.random()}`
            const alert = {
              id,
              code: event.code,
              channel: event.channel,
              time: new Date().toLocaleTimeString('id-ID', { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
            }
            setAlerts(prev => [alert, ...prev].slice(0, 5))
            alertTimers.current[id] = setTimeout(() => dismissAlert(id), ALERT_TTL)
          }
        } catch { /* ignore malformed */ }
      }
    }
    connect()
    return () => {
      es?.close()
      Object.values(alertTimers.current).forEach(clearTimeout)
    }
  }, [])


  const reload = () => fetchCameras().then(setCams).catch(e => setError(e.message))

  useEffect(() => { reload() }, [])
  useEffect(() => {
    fetchMode().then(d => setMode(d.mode)).catch(() => {})
  }, [])

  const handleModeChange = (newMode) => {
    setModeLoading(true)
    postMode(newMode)
      .then(d => setMode(d.mode))
      .catch(() => {})
      .finally(() => setModeLoading(false))
  }

  const handleAdd = async (name, rtspUrl, channel) => {
    await addCamera(name, rtspUrl, channel)
    await reload()
  }

  const handleRemove = async (id) => {
    await deleteCamera(id)
    setCams(prev => prev.filter(c => c.id !== id))
  }

  const cols = VIEW_MODES.find(m => m.id === viewMode)?.cols ?? 2

  return (
    <div className="app">
      {/* Header */}
      <header className="header">
        <div className="header-logo">
          <span>🏠</span> Home Dashboard
        </div>
        <div className="header-right">
          <Clock />
          {mode === 'away' && (
            <span className="header-mode-badge away" title="Mode Pergi — alarm aktif">🚨 PERGI</span>
          )}
          <button
            className="btn btn-ghost"
            style={{ fontSize: '.82rem', padding: '4px 10px' }}
            onClick={onConfig}
            title="Konfigurasi AI Deteksi"
          >
            ⚙
          </button>
          <div className="online-dot" title="Online" />
        </div>
      </header>

      <main className="main">
        {error && (
          <div style={{ color:'var(--red)', fontSize:'.82rem', marginBottom:16, padding:'10px 14px', background:'rgba(239,68,68,.08)', borderRadius:'var(--r-sm)', border:'1px solid rgba(239,68,68,.2)' }}>
            ⚠ Backend: {error}
          </div>
        )}

        {/* Two-column layout: sidebar (1/3) + CCTV (2/3) */}
        <div className="dashboard-grid">

          {/* ── Sidebar ── */}
          <aside className="sidebar">
            <div className="section">
              <div className="section-header">
                <div className="section-title">Cuaca</div>
              </div>
              <WeatherWidget />
            </div>

            <div className="section">
              <div className="section-header">
                <div className="section-title">Smart Home</div>
              </div>
              <SmartControls mode={mode} modeLoading={modeLoading} onModeChange={handleModeChange} />
            </div>

            <div className="section">
              <div className="section-header">
                <div className="section-title">🚪 Door Lock</div>
              </div>
              <DoorLockPanel />
            </div>

            <div className="section">
              <div className="section-header">
                <div className="section-title">Monitor</div>
              </div>
              <PerformanceMonitor />
            </div>
          </aside>

          {/* ── CCTV ── */}
          <section className="cctv-panel">
            <div className="section-header" style={{ marginBottom: 14 }}>
              <div className="section-title">CCTV · {cams.length} channel</div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                {/* View mode toggles */}
                <div className="view-modes">
                  {VIEW_MODES.map(m => (
                    <button
                      key={m.id}
                      className={`view-mode-btn${viewMode === m.id ? ' active' : ''}`}
                      title={m.label}
                      onClick={() => setViewMode(m.id)}
                    >
                      {m.icon}
                    </button>
                  ))}
                </div>
                <StreamStatusPanel
                  cams={cams}
                  open={showStatus}
                  onToggle={() => setShowStatus(o => !o)}
                />
                <button className="btn btn-ghost" onClick={() => setShowModal(true)}>
                  ＋ Tambah
                </button>
              </div>
            </div>

            {cams.length === 0 ? (
              <div style={{ color:'var(--text-muted)', fontSize:'.85rem', padding:'32px', textAlign:'center', background:'var(--surface)', borderRadius:'var(--r)', border:'1px dashed var(--border)' }}>
                Belum ada channel. Klik "Tambah" untuk menambahkan.
              </div>
            ) : (
              <div className={`video-grid cols-${cols}`}>
                {cams.map(cam => (
                  <CCTVPlayer
                    key={cam.id}
                    src={cam.stream_url}
                    name={cam.name}
                    removable={!cam.builtin}
                    onRemove={() => handleRemove(cam.id)}
                  />
                ))}
              </div>
            )}
          </section>

        </div>
      </main>

      {showModal && (
        <AddChannelModal onAdd={handleAdd} onClose={() => setShowModal(false)} />
      )}
      <AlertToasts alerts={alerts} onDismiss={dismissAlert} />
    </div>
  )
}
