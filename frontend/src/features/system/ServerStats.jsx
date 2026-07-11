import React, { useEffect, useState } from 'react'
import { fetchServerStats } from '../../services/api'

const POLL_MS = 10000

function formatBytes(value) {
  if (value == null) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let size = value
  let idx = 0
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024
    idx += 1
  }
  return `${size.toFixed(idx < 2 ? 0 : 1)} ${units[idx]}`
}

function barClass(percent) {
  if (percent == null) return ''
  if (percent >= 90) return ' crit'
  if (percent >= 70) return ' warn'
  return ''
}

function StatBar({ label, percent, detail }) {
  return (
    <div className="stat-row">
      <div className="stat-row-head">
        <span>{label}</span>
        <strong>{detail}</strong>
      </div>
      <div className="stat-bar">
        <div
          className={`stat-bar-fill${barClass(percent)}`}
          style={{ width: `${Math.min(percent ?? 0, 100)}%` }}
        />
      </div>
    </div>
  )
}

export default function ServerStats() {
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetchServerStats()
        .then(data => {
          if (!alive) return
          setStats(data)
          setError(false)
          setLastUpdate(new Date())
        })
        .catch(() => { if (alive) setError(true) })
    }
    load()
    const id = setInterval(load, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [])

  if (!stats && !error) {
    return <div className="server-stats"><div className="server-stats-loading">Memuat resource server…</div></div>
  }
  if (error && !stats) {
    return <div className="server-stats"><div className="server-stats-loading">⚠ Gagal memuat resource server</div></div>
  }

  const host = stats.host
  const memPercent = host && host.mem_total > 0 ? (host.mem_used / host.mem_total) * 100 : null
  const diskPercent = host && host.disk_total > 0 ? (host.disk_used / host.disk_total) * 100 : null

  return (
    <div className="server-stats">
      {host && (
        <>
          <StatBar
            label={`CPU · ${host.cpu_count} core`}
            percent={host.cpu_percent}
            detail={host.cpu_percent != null ? `${host.cpu_percent}%` : '—'}
          />
          <StatBar
            label="RAM"
            percent={memPercent}
            detail={`${formatBytes(host.mem_used)} / ${formatBytes(host.mem_total)}`}
          />
          <StatBar
            label="Disk data"
            percent={diskPercent}
            detail={`${formatBytes(host.disk_used)} / ${formatBytes(host.disk_total)}`}
          />
        </>
      )}

      {stats.docker_available && stats.containers.length > 0 && (
        <>
          <div className="server-stats-divider" />
          {stats.containers.map(c => (
            <div key={c.name} className="container-stat-row" title={c.status}>
              <span className={`container-stat-dot ${c.state === 'running' ? 'running' : 'stopped'}`} />
              <span className="container-stat-name">{c.name}</span>
              <span className="container-stat-vals">
                {c.state === 'running'
                  ? `${c.cpu_percent != null ? `${c.cpu_percent}%` : '—'} · ${formatBytes(c.mem_used)}`
                  : c.state}
              </span>
            </div>
          ))}
        </>
      )}

      {lastUpdate && (
        <div className="server-stats-footer">
          Update {lastUpdate.toLocaleTimeString('id-ID')}
        </div>
      )}
    </div>
  )
}
