import React, { useEffect, useState } from 'react'
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
}

function relTime(isoStr) {
  const s = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000)
  if (s < 5)    return 'baru saja'
  if (s < 60)   return `${s} dtk lalu`
  if (s < 3600) return `${Math.floor(s / 60)} mnt lalu`
  return `${Math.floor(s / 3600)} jam lalu`
}

export default function NVREventLog() {
  const [data, setData]       = useState(null)
  const [tick, setTick]       = useState(0)   // force re-render for relative times
  const [selectedCodes, setSelectedCodes] = useState([])

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
              key={i}
              className={`nvr-event-row ${ev.action === 'Start' ? 'nvr-ev-start' : 'nvr-ev-stop'}`}
            >
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
    </div>
  )
}
