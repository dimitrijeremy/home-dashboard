import React, { useEffect, useState } from 'react'
import { fetchDetectionEvents, clearDetectionEvents } from '../../services/api'

const EVENT_LABEL = {
  ZoneIntrusion:   { label: 'Masuk Zona',    icon: '⚠',  cls: 'ev-zone' },
  FaceRecognized:  { label: 'Wajah Dikenal', icon: '✅', cls: 'ev-face-ok' },
  UnknownFace:     { label: 'Wajah Asing',   icon: '❓', cls: 'ev-face-unk' },
}

const PAGE_SIZE = 30

function fmt(isoStr) {
  try {
    const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z')
    return d.toLocaleString('id-ID', { dateStyle: 'short', timeStyle: 'medium' })
  } catch { return isoStr }
}

export default function EventHistory() {
  const [data, setData]       = useState({ total: 0, events: [] })
  const [offset, setOffset]   = useState(0)
  const [loading, setLoading] = useState(true)
  const [clearing, setClearing] = useState(false)

  const load = (off = 0) => {
    setLoading(true)
    fetchDetectionEvents(PAGE_SIZE, off)
      .then(d => { setData(d); setOffset(off) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }

  useEffect(() => { load(0) }, [])

  const handleClear = async () => {
    if (!confirm('Hapus semua riwayat deteksi?')) return
    setClearing(true)
    await clearDetectionEvents().catch(() => {})
    setClearing(false)
    load(0)
  }

  const hasNext = offset + PAGE_SIZE < data.total
  const hasPrev = offset > 0

  return (
    <div className="event-history">
      <div className="ev-hist-header">
        <span className="ev-hist-count">{data.total} event tersimpan</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-ghost" onClick={() => load(offset)} disabled={loading}>
            ↻ Refresh
          </button>
          {data.total > 0 && (
            <button className="btn btn-red" onClick={handleClear} disabled={clearing}>
              {clearing ? 'Menghapus…' : '🗑 Hapus Semua'}
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="ev-hist-loading">Memuat…</div>
      ) : data.events.length === 0 ? (
        <div className="ev-hist-empty">
          Belum ada event. Aktifkan analyzer service untuk mulai deteksi.
        </div>
      ) : (
        <>
          <div className="ev-hist-table-wrap">
            <table className="ev-hist-table">
              <thead>
                <tr>
                  <th>Waktu</th>
                  <th>Channel</th>
                  <th>Tipe</th>
                  <th>Zona</th>
                  <th>Orang</th>
                  <th>Conf</th>
                </tr>
              </thead>
              <tbody>
                {data.events.map(ev => {
                  const meta = EVENT_LABEL[ev.event_type] || { label: ev.event_type, icon: '●', cls: '' }
                  return (
                    <tr key={ev.id} className={meta.cls}>
                      <td className="ev-ts">{fmt(ev.ts)}</td>
                      <td className="ev-ch">{ev.channel_id || ev.camera_name}</td>
                      <td className="ev-type">
                        <span className={`ev-type-badge ${meta.cls}`}>
                          {meta.icon} {meta.label}
                        </span>
                      </td>
                      <td className="ev-zone">{ev.zone_name || '—'}</td>
                      <td className="ev-person">{ev.person_name || <span style={{ color: 'var(--text-muted)' }}>Tidak dikenal</span>}</td>
                      <td className="ev-conf">
                        {ev.confidence != null ? `${(ev.confidence * 100).toFixed(0)}%` : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {(hasPrev || hasNext) && (
            <div className="ev-hist-pagination">
              <button className="btn btn-ghost" disabled={!hasPrev} onClick={() => load(offset - PAGE_SIZE)}>
                ← Sebelumnya
              </button>
              <span className="ev-page-info">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} dari {data.total}
              </span>
              <button className="btn btn-ghost" disabled={!hasNext} onClick={() => load(offset + PAGE_SIZE)}>
                Berikutnya →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}
