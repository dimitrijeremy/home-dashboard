import React, { useEffect, useState } from 'react'
import { fetchPerformance } from '../../services/api'

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = bytes
  let idx = 0
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024
    idx++
  }
  return `${size.toFixed(idx < 2 ? 0 : 1)} ${units[idx]}`
}

function formatUptime(seconds) {
  if (!seconds) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}h ${h}j ${m}m`
  if (h > 0) return `${h}j ${m}m`
  return `${m}m`
}

function MiniBar({ percent, color }) {
  return (
    <div className="perf-bar-track">
      <div
        className="perf-bar-fill"
        style={{ width: `${Math.min(percent || 0, 100)}%`, background: color || 'var(--accent)' }}
      />
    </div>
  )
}

export default function PerformanceMonitor() {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState(null)

  const refresh = () => {
    setLoading(true)
    fetchPerformance()
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 15000) // refresh every 15s
    return () => clearInterval(id)
  }, [])

  if (loading && !data) {
    return <div className="perf-widget"><div className="perf-loading">Memuat…</div></div>
  }

  if (error && !data) {
    return <div className="perf-widget"><div className="perf-error">⚠ {error}</div></div>
  }

  const { server, nvr } = data || {}

  const cpuColor = (pct) => {
    if (pct > 80) return 'var(--red)'
    if (pct > 60) return 'var(--yellow)'
    return 'var(--green)'
  }

  return (
    <div className="perf-widget">
      <div className="perf-header">
        <span className="perf-title">📊 Performa</span>
        <button className="btn-icon" onClick={refresh} title="Refresh" style={{ fontSize: '.7rem' }}>↻</button>
      </div>

      {/* Server metrics */}
      {server && (
        <div className="perf-section">
          <div className="perf-section-label">Server</div>
          <div className="perf-row">
            <span>CPU</span>
            <MiniBar percent={server.cpu_percent} color={cpuColor(server.cpu_percent)} />
            <span className="perf-val">{server.cpu_percent != null ? `${server.cpu_percent}%` : '—'}</span>
          </div>
          <div className="perf-row">
            <span>RAM</span>
            <MiniBar percent={server.mem_percent} color={cpuColor(server.mem_percent)} />
            <span className="perf-val">{server.mem_percent != null ? `${server.mem_percent}%` : '—'}</span>
          </div>
          <div className="perf-row">
            <span>Disk</span>
            <MiniBar percent={server.disk_percent} color={cpuColor(server.disk_percent)} />
            <span className="perf-val">{server.disk_percent != null ? `${server.disk_percent}%` : '—'}</span>
          </div>
          {server.uptime_seconds != null && (
            <div className="perf-row">
              <span>Uptime</span>
              <span className="perf-val" style={{ marginLeft: 'auto' }}>{formatUptime(server.uptime_seconds)}</span>
            </div>
          )}
        </div>
      )}

      {/* NVR metrics */}
      {nvr && (
        <div className="perf-section">
          <div className="perf-section-label">NVR</div>
          {nvr.reachable ? (
            <>
              {nvr.cpu_percent != null && (
                <div className="perf-row">
                  <span>CPU</span>
                  <MiniBar percent={nvr.cpu_percent} color={cpuColor(nvr.cpu_percent)} />
                  <span className="perf-val">{nvr.cpu_percent}%</span>
                </div>
              )}
              {nvr.mem_total != null && nvr.mem_total > 0 && (
                <div className="perf-row">
                  <span>RAM</span>
                  <MiniBar
                    percent={nvr.mem_used / nvr.mem_total * 100}
                    color={cpuColor(nvr.mem_used / nvr.mem_total * 100)}
                  />
                  <span className="perf-val">{formatBytes(nvr.mem_used)} / {formatBytes(nvr.mem_total)}</span>
                </div>
              )}
            </>
          ) : (
            <div className="perf-row" style={{ color: 'var(--text-muted)' }}>
              <span>● Tidak terhubung</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
