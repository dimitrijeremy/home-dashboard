import React, { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fetchNvrEvents, nvrEventsStreamUrl } from '../../services/api'

const CODE_LABEL = {
  VideoMotion:          'Motion',
  SmartMotionHuman:     'Manusia',
  SmartMotionVehicle:   'Kendaraan',
  VideoLoss:            'No Signal',
  VideoBlind:           'Kamera Tertutup',
  CrossLineDetection:   'Line Crossing',
  CrossRegionDetection: 'Intrusion',
  AlarmLocal:           'Alarm Lokal',
  DiskFull:             'Disk Penuh',
  DiskError:            'Disk Error',
  NetAbort:             'Net Putus',
  ZoneIntrusion:        'Intrusion AI',
  UnknownFace:          'Wajah Asing',
  FaceRecognized:       'Wajah Dikenal',
}

const FILTER_STORAGE_KEY = 'hd_nvr_filter_codes'
const DEFAULT_FILTER_CODES = [
  'SmartMotionHuman',
  'VideoMotion',
  'CrossRegionDetection',
  'ZoneIntrusion',
]

function relTime(isoStr) {
  const s = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (s < 5)    return 'baru saja'
  if (s < 60)   return `${s} dtk lalu`
  if (s < 3600) return `${Math.floor(s / 60)} mnt lalu`
  return `${Math.floor(s / 3600)} jam lalu`
}

// Clip event dibuat asinkron setelah event masuk, dan dibuang lagi oleh janitor
// backend saat budget storage terlampaui (lihat MONITORING.md § 5). Tanpa
// pembeda umur, event lama yang clipnya sudah dihapus akan selamanya bilang
// "sedang disiapkan" — padahal tidak akan pernah datang.
const CLIP_PENDING_WINDOW_SECS = 300

function clipIsStillPending(isoStr) {
  const age = (Date.now() - new Date(isoStr).getTime()) / 1000
  return Number.isFinite(age) && age < CLIP_PENDING_WINDOW_SECS
}

function absTime(isoStr) {
  return new Date(isoStr).toLocaleString('id-ID', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function loadSelectedCodes() {
  if (typeof window === 'undefined') return DEFAULT_FILTER_CODES
  try {
    const raw = localStorage.getItem(FILTER_STORAGE_KEY)
    if (raw == null) return DEFAULT_FILTER_CODES
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : DEFAULT_FILTER_CODES
  } catch {
    return DEFAULT_FILTER_CODES
  }
}

function channelLabel(ev) {
  const channelNumber = ev.channel_number ?? (typeof ev.index === 'number' ? ev.index + 1 : null)
  return channelNumber ? `Ch${channelNumber}` : '—'
}

export default function NVREventLog() {
  const [data, setData]       = useState(null)
  const [tick, setTick]       = useState(0)   // force re-render for relative times
  const [selectedCodes, setSelectedCodes] = useState(loadSelectedCodes)
  const [hoveredEventId, setHoveredEventId] = useState(null)
  const [previewEvent, setPreviewEvent] = useState(null)
  const [previewZoom, setPreviewZoom] = useState(1.4)
  const [previewMode, setPreviewMode] = useState('image')
  const [isPanning, setIsPanning] = useState(false)
  const stageRef = useRef(null)
  const dragRef = useRef(null)

  useEffect(() => {
    let alive = true
    let fallbackId = null
    let source = null

    const startFallback = () => {
      if (fallbackId) return
      const poll = () => {
        fetchNvrEvents()
          .then(d => { if (alive) setData(d) })
          .catch(() => {})
      }
      poll()
      fallbackId = setInterval(poll, 3000)
    }

    if (typeof EventSource === 'function') {
      source = new EventSource(nvrEventsStreamUrl())
      source.onmessage = (event) => {
        if (!alive) return
        try { setData(JSON.parse(event.data)) }
        catch { /* ignore malformed event */ }
      }
      source.onerror = () => {
        source.close()
        startFallback()
      }
    } else {
      startFallback()
    }

    const tickId = setInterval(() => { if (alive) setTick(n => n + 1) }, 10000)
    return () => {
      alive = false
      if (source) source.close()
      if (fallbackId) clearInterval(fallbackId)
      clearInterval(tickId)
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(selectedCodes))
  }, [selectedCodes])

  const connected = data?.status?.connected
  const events    = data?.events ?? []
  const availableCodes = Array.from(new Set([
    ...Object.keys(CODE_LABEL),
    ...events.map(ev => ev.code).filter(Boolean),
  ])).sort((a, b) => (CODE_LABEL[a] ?? a).localeCompare(CODE_LABEL[b] ?? b))
  const selectedSet = new Set(selectedCodes)
  const filteredEvents = selectedCodes.length === 0
    ? events
    : events.filter(ev => selectedSet.has(ev.code))
  const lastEvents = filteredEvents.slice(0, 30)

  // Compute per-(channel,code) active state: true if the latest event for that pair is Start
  const activeMap = {}
  for (const ev of [...filteredEvents].reverse()) {
    const key = `${ev.index}:${ev.code}`
    activeMap[key] = ev.action === 'Start'
  }

  const toggleCode = (code) => {
    setSelectedCodes(prev => (
      prev.includes(code)
        ? prev.filter(item => item !== code)
        : [...prev, code]
    ))
  }

  const openPreview = (ev) => {
    if (!ev.snapshot_url && !ev.clip_url) return
    setPreviewEvent(ev)
    setPreviewZoom(1.4)
    setPreviewMode(ev.snapshot_url ? 'image' : 'video')
    setIsPanning(false)
  }

  useEffect(() => {
    if (!previewEvent) return
    const freshEvent = events.find(ev => ev.id === previewEvent.id)
    if (!freshEvent) return
    const next = JSON.stringify(freshEvent)
    const current = JSON.stringify(previewEvent)
    if (next !== current) setPreviewEvent(freshEvent)
  }, [events, previewEvent])

  useEffect(() => {
    if (!previewEvent || previewMode !== 'image') return
    const stage = stageRef.current
    if (!stage) return
    requestAnimationFrame(() => {
      if (previewZoom <= 1) {
        stage.scrollTo({ left: 0, top: 0 })
        return
      }
      const box = previewEvent.detection_box
      const maxLeft = Math.max(stage.scrollWidth - stage.clientWidth, 0)
      const maxTop = Math.max(stage.scrollHeight - stage.clientHeight, 0)
      if (box && typeof box.left === 'number' && typeof box.right === 'number') {
        const midX = Math.max(0, Math.min(1, (box.left + box.right) / 2))
        const midY = Math.max(0, Math.min(1, (box.top + box.bottom) / 2))
        stage.scrollLeft = maxLeft * midX
        stage.scrollTop = maxTop * midY
        return
      }
      stage.scrollLeft = maxLeft / 2
      stage.scrollTop = 0
    })
  }, [previewEvent, previewZoom, previewMode])

  useEffect(() => {
    if (!isPanning) return undefined
    const handleMove = (event) => {
      const drag = dragRef.current
      const stage = stageRef.current
      if (!drag || !stage) return
      stage.scrollLeft = drag.startLeft - (event.clientX - drag.startX)
      stage.scrollTop = drag.startTop - (event.clientY - drag.startY)
    }
    const handleUp = () => {
      dragRef.current = null
      setIsPanning(false)
    }
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', handleUp)
    return () => {
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', handleUp)
    }
  }, [isPanning])

  const startPan = (event) => {
    if (previewMode !== 'image' || previewZoom <= 1) return
    const stage = stageRef.current
    if (!stage) return
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      startLeft: stage.scrollLeft,
      startTop: stage.scrollTop,
    }
    setIsPanning(true)
  }

  const zonePoints = Array.isArray(previewEvent?.zone_points) ? previewEvent.zone_points : []
  const clipPending = !previewEvent?.clip_url && clipIsStillPending(previewEvent?.ts)
  const detectionBox = previewEvent?.detection_box
  const previewCanvasWidth = `${Math.max(previewZoom * 100, 100)}%`

  const previewModal = previewEvent && typeof document !== 'undefined' ? createPortal(
    <div className="modal-overlay nvr-preview-overlay" onClick={() => setPreviewEvent(null)}>
      <div className="modal nvr-preview-modal" onClick={(event) => event.stopPropagation()}>
        <div className="nvr-preview-header">
          <div>
            <div className="nvr-preview-title">{CODE_LABEL[previewEvent.code] ?? previewEvent.code}</div>
            <div className="nvr-preview-subtitle">
              {channelLabel(previewEvent)} · {absTime(previewEvent.ts)}
            </div>
          </div>
          <button className="btn-icon" onClick={() => setPreviewEvent(null)}>✕</button>
        </div>

        <div className="nvr-preview-toolbar">
          <div className="nvr-preview-mode-buttons">
            <button
              className={`btn btn-ghost${previewMode === 'image' ? ' is-active' : ''}`}
              disabled={!previewEvent.snapshot_url}
              onClick={() => setPreviewMode('image')}
            >
              Screenshot
            </button>
            <button
              className={`btn btn-ghost${previewMode === 'video' ? ' is-active' : ''}`}
              disabled={!previewEvent.clip_url}
              onClick={() => setPreviewMode('video')}
              title={
                previewEvent.clip_url
                  ? 'Putar clip event'
                  : clipPending
                    ? 'Clip sedang disiapkan'
                    : 'Clip tidak tersimpan untuk event ini'
              }
            >
              {previewEvent.clip_url
                ? 'Playback Event'
                : clipPending ? 'Menyiapkan Playback…' : 'Playback Tidak Ada'}
            </button>
          </div>

          {previewMode === 'image' && previewEvent.snapshot_url && (
            <>
              <label htmlFor="nvr-preview-zoom">Zoom {previewZoom.toFixed(1)}x</label>
              <input
                id="nvr-preview-zoom"
                type="range"
                min="1"
                max="3"
                step="0.1"
                value={previewZoom}
                onChange={(event) => setPreviewZoom(Number(event.target.value))}
              />
            </>
          )}
        </div>

        <div
          ref={stageRef}
          className={`nvr-preview-stage${previewMode === 'image' && previewZoom > 1 ? ' is-pannable' : ''}${isPanning ? ' is-panning' : ''}`}
          onMouseDown={startPan}
        >
          {previewMode === 'video' && previewEvent.clip_url ? (
            <video className="nvr-preview-video" controls autoPlay src={previewEvent.clip_url} />
          ) : previewEvent.snapshot_url ? (
            <div className="nvr-preview-image-wrap">
              <div className="nvr-preview-canvas" style={{ width: previewCanvasWidth }}>
                <img
                  src={previewEvent.snapshot_url}
                  alt={`${CODE_LABEL[previewEvent.code] ?? previewEvent.code} ${channelLabel(previewEvent)}`}
                  draggable="false"
                />
                {zonePoints.length >= 3 && (
                  <svg className="nvr-preview-zone-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                    <polygon points={zonePoints.map(([x, y]) => `${x * 100},${y * 100}`).join(' ')} />
                  </svg>
                )}
                {detectionBox && (
                  <div
                    className="nvr-preview-detection-box"
                    style={{
                      left: `${Math.max(detectionBox.left, 0) * 100}%`,
                      top: `${Math.max(detectionBox.top, 0) * 100}%`,
                      width: `${Math.max((detectionBox.right - detectionBox.left) * 100, 0)}%`,
                      height: `${Math.max((detectionBox.bottom - detectionBox.top) * 100, 0)}%`,
                    }}
                  />
                )}
              </div>
            </div>
          ) : (
            <div className="nvr-event-empty">Snapshot tidak tersedia untuk event ini.</div>
          )}
        </div>

        <div className="nvr-preview-meta-panel">
          <span>{previewEvent.source === 'analyzer' ? 'Sumber: AI' : 'Sumber: NVR'}</span>
          {previewEvent.person && <span>Orang: {previewEvent.person}</span>}
          {previewEvent.zone_name && <span>Zona: {previewEvent.zone_name}</span>}
          {previewEvent.clip_url
            ? <span>Playback clip lokal siap diputar.</span>
            : clipPending
              ? <span>Playback clip sedang disiapkan…</span>
              : <span>Clip tidak tersimpan untuk event ini — sudah dilewat cooldown capture atau dibuang housekeeping storage.</span>}
          {previewMode === 'image' && previewZoom > 1 && <span>Geser gambar dengan drag untuk melihat area lain.</span>}
        </div>
      </div>
    </div>,
    document.body,
  ) : null

  return (
    <div className="nvr-event-log">
      <div className="nvr-event-header">
        <span className="nvr-event-title">Deteksi NVR</span>
        <span className={`nvr-conn-badge ${data === null ? 'nvr-conn-wait' : connected ? 'nvr-conn-ok' : 'nvr-conn-err'}`}>
          {data === null ? '…' : connected ? '● Live' : '○ Terputus'}
        </span>
      </div>

      <div className="nvr-filter-row">
        <button
          className={`nvr-filter-chip${selectedCodes.length === 0 ? ' active' : ''}`}
          onClick={() => setSelectedCodes([])}
        >
          Semua
        </button>
        {availableCodes.map(code => (
          <button
            key={code}
            className={`nvr-filter-chip${selectedSet.has(code) ? ' active' : ''}`}
            onClick={() => toggleCode(code)}
            title={code}
          >
            {CODE_LABEL[code] ?? code}
          </button>
        ))}
      </div>

      {/* Active detections summary row */}
      {Object.entries(activeMap).some(([, v]) => v) && (
        <div className="nvr-active-row">
          {Object.entries(activeMap)
            .filter(([, v]) => v)
            .map(([key]) => {
              const [idx, code] = key.split(':')
              return (
                <span key={key} className="nvr-active-badge">
                  {CODE_LABEL[code] ?? code} Ch{Number(idx) + 1}
                </span>
              )
            })}
        </div>
      )}

      {lastEvents.length === 0 ? (
        <div className="nvr-event-empty">
          {data === null
            ? 'Menghubungkan…'
            : connected
            ? selectedCodes.length > 0 ? 'Tidak ada event untuk filter ini' : 'Menunggu event…'
            : (data?.status?.error ?? 'Tidak terhubung ke NVR')}
        </div>
      ) : (
        <div className="nvr-event-list">
          {lastEvents.map((ev, i) => (
            <div
              key={ev.id ?? i}
              className={`nvr-event-row ${ev.action === 'Start' ? 'nvr-ev-start' : 'nvr-ev-stop'}${ev.snapshot_url ? ' has-preview' : ''}`}
              onMouseEnter={() => ev.snapshot_url && setHoveredEventId(ev.id)}
              onMouseLeave={() => setHoveredEventId(prev => (prev === ev.id ? null : prev))}
              onClick={() => openPreview(ev)}
            >
              <span className="nvr-ev-code">{CODE_LABEL[ev.code] ?? ev.code}</span>
              <span className="nvr-ev-ch">{channelLabel(ev)}</span>
              <span className={`nvr-ev-action ${ev.action === 'Start' ? 'nvr-action-start' : 'nvr-action-stop'}`}>
                {ev.action === 'Start' ? '▶' : '■'}
              </span>
              <span className="nvr-ev-time" title={absTime(ev.ts)}>{relTime(ev.ts)}</span>
              {(ev.person || ev.zone_name || ev.snapshot_url) && (
                <div className="nvr-ev-meta">
                  {ev.person && <span>{ev.person}</span>}
                  {ev.zone_name && <span>{ev.zone_name}</span>}
                  {ev.snapshot_url && <span className="nvr-ev-preview-tag">SS</span>}
                  <span>{ev.source === 'analyzer' ? 'AI' : 'NVR'}</span>
                </div>
              )}
              {ev.snapshot_url && hoveredEventId === ev.id && (
                <div className="nvr-event-hover-preview">
                  <img src={ev.snapshot_url} alt={`${CODE_LABEL[ev.code] ?? ev.code} ${channelLabel(ev)}`} loading="lazy" />
                  <div className="nvr-event-hover-caption">Klik untuk zoom</div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {previewModal}
    </div>
  )
}
