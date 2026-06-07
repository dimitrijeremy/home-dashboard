import React, { useEffect, useState, useCallback } from 'react'
import { fetchNvrEvents, fetchNvrHistory, fetchNvrPlaybackEvents, nvrEventsStreamUrl } from '../../services/api'

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
}

function relTime(isoStr) {
  const s = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (s < 5)    return 'baru saja'
  if (s < 60)   return `${s} dtk lalu`
  if (s < 3600) return `${Math.floor(s / 60)} mnt lalu`
  if (s < 86400) return `${Math.floor(s / 3600)} jam lalu`
  return new Date(isoStr).toLocaleDateString('id-ID', { day: '2-digit', month: 'short' })
}

function fmtTs(isoStr) {
  return new Date(isoStr).toLocaleString('id-ID', {
    day: '2-digit', month: 'short', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

// Return ISO string for start-of-day N days ago (local time → UTC)
function daysAgoISO(n) {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 16)  // "YYYY-MM-DDTHH:MM"
}

export default function NVREventLog() {
  const [tab, setTab]             = useState('live')   // 'live' | 'history' | 'playback'
  const [data, setData]           = useState(null)
  const [tick, setTick]           = useState(0)
  const [selectedCodes, setSelectedCodes] = useState([])

  // History tab state
  const [histFrom, setHistFrom]   = useState(daysAgoISO(1))
  const [histTo, setHistTo]       = useState('')
  const [histCode, setHistCode]   = useState('')
  const [histRows, setHistRows]   = useState(null)
  const [histLoading, setHistLoading] = useState(false)
  const [histError, setHistError] = useState('')

  // Playback search state (NVR recorded event clips)
  const [playFrom, setPlayFrom]   = useState(daysAgoISO(1))
  const [playTo, setPlayTo]       = useState('')
  const [playCode, setPlayCode]   = useState('VideoMotion')
  const [playChannel, setPlayChannel] = useState('-1')
  const [playRows, setPlayRows]   = useState(null)
  const [playQuery, setPlayQuery] = useState(null)
  const [playLoading, setPlayLoading] = useState(false)
  const [playError, setPlayError] = useState('')

  // Live SSE feed
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
      source.onerror = () => { source.close(); startFallback() }
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

  const loadHistory = useCallback(() => {
    setHistLoading(true)
    setHistError('')
    fetchNvrHistory({
      from:  histFrom  ? new Date(histFrom).toISOString()  : undefined,
      to:    histTo    ? new Date(histTo + ':59').toISOString() : undefined,
      code:  histCode  || undefined,
      limit: 300,
    })
      .then(rows => { setHistRows(rows); setHistLoading(false) })
      .catch(err => { setHistError(err.message); setHistLoading(false) })
  }, [histFrom, histTo, histCode])

  const loadPlayback = useCallback(() => {
    setPlayLoading(true)
    setPlayError('')
    fetchNvrPlaybackEvents({
      from: playFrom ? playFrom.replace('T', ' ') + ':00' : undefined,
      to: playTo ? playTo.replace('T', ' ') + ':59' : undefined,
      code: playCode || undefined,
      channel: parseInt(playChannel, 10),
      limit: 120,
    })
      .then(payload => {
        setPlayRows(payload.events || [])
        setPlayQuery(payload.query || null)
        setPlayLoading(false)
      })
      .catch(err => {
        setPlayError(err.message)
        setPlayLoading(false)
      })
  }, [playFrom, playTo, playCode, playChannel])

  // Auto-load history when switching to that tab
  useEffect(() => {
    if (tab === 'history' && histRows === null) loadHistory()
  }, [tab, histRows, loadHistory])

  useEffect(() => {
    if (tab === 'playback' && playRows === null) loadPlayback()
  }, [tab, playRows, loadPlayback])

  // ─── Live tab ───────────────────────────────────────────────────────────────
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

  const activeMap = {}
  for (const ev of [...filteredEvents].reverse()) {
    const key = `${ev.index}:${ev.code}`
    activeMap[key] = ev.action === 'Start'
  }

  const toggleCode = (code) => {
    setSelectedCodes(prev => (
      prev.includes(code) ? prev.filter(c => c !== code) : [...prev, code]
    ))
  }

  // ─── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="nvr-event-log">
      <div className="nvr-event-header">
        <span className="nvr-event-title">Deteksi NVR</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span className={`nvr-conn-badge ${data === null ? 'nvr-conn-wait' : connected ? 'nvr-conn-ok' : 'nvr-conn-err'}`}>
            {data === null ? '…' : connected ? '● Live' : '○ Terputus'}
          </span>
          <button
            className={`nvr-tab-btn${tab === 'live' ? ' active' : ''}`}
            onClick={() => setTab('live')}
          >Live</button>
          <button
            className={`nvr-tab-btn${tab === 'history' ? ' active' : ''}`}
            onClick={() => setTab('history')}
          >Riwayat</button>
          <button
            className={`nvr-tab-btn${tab === 'playback' ? ' active' : ''}`}
            onClick={() => setTab('playback')}
          >Playback</button>
        </div>
      </div>

      {/* ── LIVE TAB ── */}
      {tab === 'live' && (
        <>
          <div className="nvr-filter-row">
            <button
              className={`nvr-filter-chip${selectedCodes.length === 0 ? ' active' : ''}`}
              onClick={() => setSelectedCodes([])}
            >Semua</button>
            {availableCodes.map(code => (
              <button
                key={code}
                className={`nvr-filter-chip${selectedSet.has(code) ? ' active' : ''}`}
                onClick={() => toggleCode(code)}
                title={code}
              >{CODE_LABEL[code] ?? code}</button>
            ))}
          </div>

          {Object.entries(activeMap).some(([, v]) => v) && (
            <div className="nvr-active-row">
              {Object.entries(activeMap).filter(([, v]) => v).map(([key]) => {
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
              {data === null ? 'Menghubungkan…'
                : connected
                  ? selectedCodes.length > 0 ? 'Tidak ada event untuk filter ini' : 'Menunggu event…'
                  : (data?.status?.error ?? 'Tidak terhubung ke NVR')}
            </div>
          ) : (
            <div className="nvr-event-list">
              {lastEvents.map((ev, i) => (
                <div key={i} className={`nvr-event-row ${ev.action === 'Start' ? 'nvr-ev-start' : 'nvr-ev-stop'}`}>
                  <span className="nvr-ev-code">{CODE_LABEL[ev.code] ?? ev.code}</span>
                  <span className="nvr-ev-ch">Ch{ev.index + 1}</span>
                  <span className={`nvr-ev-action ${ev.action === 'Start' ? 'nvr-action-start' : 'nvr-action-stop'}`}>
                    {ev.action === 'Start' ? '▶' : '■'}
                  </span>
                  <span className="nvr-ev-time">{relTime(ev.ts)}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* ── HISTORY TAB ── */}
      {tab === 'history' && (
        <div className="nvr-history">
          <div className="nvr-hist-filters">
            <label>
              <span>Dari</span>
              <input
                type="datetime-local"
                value={histFrom}
                onChange={e => setHistFrom(e.target.value)}
              />
            </label>
            <label>
              <span>Sampai</span>
              <input
                type="datetime-local"
                value={histTo}
                onChange={e => setHistTo(e.target.value)}
              />
            </label>
            <label>
              <span>Tipe</span>
              <select value={histCode} onChange={e => setHistCode(e.target.value)}>
                <option value="">Semua</option>
                {Object.entries(CODE_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <button className="nvr-hist-search-btn" onClick={loadHistory} disabled={histLoading}>
              {histLoading ? '…' : 'Cari'}
            </button>
          </div>

          {histError && <div className="nvr-event-empty" style={{ color: 'var(--c-danger, #f44)' }}>{histError}</div>}

          {!histError && histRows !== null && histRows.length === 0 && (
            <div className="nvr-event-empty">Tidak ada event dalam rentang ini.</div>
          )}

          {histRows && histRows.length > 0 && (
            <div className="nvr-event-list">
              {histRows.map(ev => (
                <div key={ev.id} className={`nvr-event-row ${ev.action === 'Start' ? 'nvr-ev-start' : 'nvr-ev-stop'}`}>
                  <span className="nvr-ev-code">{CODE_LABEL[ev.code] ?? ev.code}</span>
                  <span className="nvr-ev-ch">Ch{ev.channel + 1}</span>
                  <span className={`nvr-ev-action ${ev.action === 'Start' ? 'nvr-action-start' : 'nvr-action-stop'}`}>
                    {ev.action === 'Start' ? '▶' : '■'}
                  </span>
                  <span className="nvr-ev-time" title={ev.ts}>{fmtTs(ev.ts)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── PLAYBACK TAB ── */}
      {tab === 'playback' && (
        <div className="nvr-history">
          <div className="nvr-hist-filters">
            <label>
              <span>Dari</span>
              <input
                type="datetime-local"
                value={playFrom}
                onChange={e => setPlayFrom(e.target.value)}
              />
            </label>
            <label>
              <span>Sampai</span>
              <input
                type="datetime-local"
                value={playTo}
                onChange={e => setPlayTo(e.target.value)}
              />
            </label>
            <label>
              <span>Channel</span>
              <select value={playChannel} onChange={e => setPlayChannel(e.target.value)}>
                <option value="-1">Semua</option>
                {Array.from({ length: 16 }, (_, i) => i + 1).map(ch => (
                  <option key={ch} value={ch}>{`Ch ${ch}`}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Event</span>
              <select value={playCode} onChange={e => setPlayCode(e.target.value)}>
                <option value="">Semua event</option>
                {Object.entries(CODE_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
            </label>
            <button className="nvr-hist-search-btn" onClick={loadPlayback} disabled={playLoading}>
              {playLoading ? '…' : 'Cari'}
            </button>
          </div>

          {playQuery && (
            <div className="nvr-play-query">
              {`${playQuery.from} → ${playQuery.to}`}
            </div>
          )}

          {playError && <div className="nvr-event-empty" style={{ color: 'var(--c-danger, #f44)' }}>{playError}</div>}

          {!playError && playRows !== null && playRows.length === 0 && (
            <div className="nvr-event-empty">Tidak ada rekaman event dalam rentang ini.</div>
          )}

          {playRows && playRows.length > 0 && (
            <div className="nvr-event-list nvr-playback-list">
              {playRows.map((ev, i) => (
                <div key={`${ev.path || ev.start_time}-${i}`} className="nvr-playback-row">
                  <div className="nvr-playback-main">
                    <span className="nvr-ev-code">{CODE_LABEL[ev.event] || ev.event || 'Event'}</span>
                    <span className="nvr-ev-ch">{Number(ev.channel) > 0 ? `Ch${ev.channel}` : 'All'}</span>
                  </div>
                  <div className="nvr-playback-time">
                    <span>{ev.start_time || '—'}</span>
                    <span>{ev.end_time || '—'}</span>
                  </div>
                  {ev.path && <div className="nvr-playback-path" title={ev.path}>{ev.path}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
