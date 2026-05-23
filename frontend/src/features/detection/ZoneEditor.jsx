import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fetchZones, addZone, deleteZone, patchZone, cameraSnapshotUrl } from '../../services/api'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']

function ZoneCanvas({ imgSize, zones, draftPoints, onCanvasClick, selectedZoneId }) {
  const canvasRef = useRef(null)

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !imgSize) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const toXY = ([xn, yn]) => [xn * canvas.width, yn * canvas.height]

    // Draw saved zones
    zones.forEach((zone, zi) => {
      if (!zone.points_json) return
      let pts
      try { pts = JSON.parse(zone.points_json) } catch { return }
      if (pts.length < 2) return

      const color = COLORS[zi % COLORS.length]
      const isSelected = zone.id === selectedZoneId
      ctx.beginPath()
      const [sx, sy] = toXY(pts[0])
      ctx.moveTo(sx, sy)
      for (let i = 1; i < pts.length; i++) {
        const [x, y] = toXY(pts[i])
        ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.fillStyle = color.replace(')', ', 0.15)').replace('rgb', 'rgba').replace('#', 'rgba(').replace('rgba(', 'rgba(')
      // simpler alpha fill:
      ctx.globalAlpha = 0.18
      ctx.fillStyle = color
      ctx.fill()
      ctx.globalAlpha = 1
      ctx.strokeStyle = isSelected ? '#fff' : color
      ctx.lineWidth = isSelected ? 2.5 : 1.5
      ctx.stroke()

      // Label
      const [lx, ly] = toXY(pts[0])
      ctx.fillStyle = '#fff'
      ctx.font = 'bold 11px Inter, sans-serif'
      ctx.shadowColor = 'rgba(0,0,0,0.8)'
      ctx.shadowBlur = 4
      ctx.fillText(zone.name, lx + 4, ly - 5)
      ctx.shadowBlur = 0
    })

    // Draw draft polygon
    if (draftPoints.length > 0) {
      ctx.beginPath()
      const [sx, sy] = toXY(draftPoints[0])
      ctx.moveTo(sx, sy)
      for (let i = 1; i < draftPoints.length; i++) {
        const [x, y] = toXY(draftPoints[i])
        ctx.lineTo(x, y)
      }
      ctx.strokeStyle = '#f59e0b'
      ctx.lineWidth = 2
      ctx.setLineDash([5, 4])
      ctx.stroke()
      ctx.setLineDash([])

      // Dots at each point
      draftPoints.forEach(([xn, yn], i) => {
        const x = xn * canvas.width
        const y = yn * canvas.height
        ctx.beginPath()
        ctx.arc(x, y, i === 0 ? 7 : 5, 0, Math.PI * 2)
        ctx.fillStyle = i === 0 ? '#f59e0b' : '#fcd34d'
        ctx.fill()
        ctx.strokeStyle = '#fff'
        ctx.lineWidth = 1.5
        ctx.stroke()
      })
    }
  }, [zones, draftPoints, imgSize, selectedZoneId])

  useEffect(() => { draw() }, [draw])

  const handleClick = (e) => {
    const canvas = canvasRef.current
    const rect = canvas.getBoundingClientRect()
    const xn = (e.clientX - rect.left) / rect.width
    const yn = (e.clientY - rect.top) / rect.height
    onCanvasClick(xn, yn)
  }

  return (
    <canvas
      ref={canvasRef}
      width={imgSize?.w || 640}
      height={imgSize?.h || 360}
      style={{
        position: 'absolute', top: 0, left: 0,
        width: '100%', height: '100%',
        cursor: 'crosshair',
      }}
      onClick={handleClick}
    />
  )
}

export default function ZoneEditor({ camera }) {
  const [zones, setZones]           = useState([])
  const [draftPoints, setDraftPoints] = useState([])
  const [draftName, setDraftName]   = useState('')
  const [selectedZone, setSelectedZone] = useState(null)
  const [imgSize, setImgSize]       = useState(null)
  const [snapError, setSnapError]   = useState(false)
  const [saving, setSaving]         = useState(false)
  const imgRef = useRef(null)

  const snapUrl = cameraSnapshotUrl(camera.id)

  useEffect(() => {
    fetchZones()
      .then(all => setZones(all.filter(z => z.camera_id === camera.id)))
      .catch(() => {})
  }, [camera.id])

  const onImgLoad = () => {
    const el = imgRef.current
    if (el) setImgSize({ w: el.naturalWidth, h: el.naturalHeight })
  }

  const handleCanvasClick = (xn, yn) => {
    // If first point clicked again (within 0.03 radius) — close polygon
    if (draftPoints.length >= 3) {
      const [fx, fy] = draftPoints[0]
      if (Math.abs(xn - fx) < 0.03 && Math.abs(yn - fy) < 0.03) {
        // Close polygon (don't add last point, polygon is closed geometrically)
        return
      }
    }
    setDraftPoints(prev => [...prev, [xn, yn]])
  }

  const handleSaveZone = async () => {
    if (draftPoints.length < 3) return
    const name = draftName.trim() || `Zona ${zones.length + 1}`
    setSaving(true)
    try {
      const z = await addZone(camera.id, name, draftPoints)
      setZones(prev => [...prev, z])
      setDraftPoints([])
      setDraftName('')
    } catch (e) {
      alert(e.message)
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteZone = async (zoneId) => {
    if (!confirm('Hapus zona ini?')) return
    await deleteZone(zoneId).catch(() => {})
    setZones(prev => prev.filter(z => z.id !== zoneId))
    if (selectedZone === zoneId) setSelectedZone(null)
  }

  const handleToggleZone = async (zone) => {
    const updated = await patchZone(zone.id, { enabled: zone.enabled ? 0 : 1 }).catch(() => null)
    if (updated) setZones(prev => prev.map(z => z.id === zone.id ? updated : z))
  }

  return (
    <div className="zone-editor">
      {/* Camera snapshot + canvas overlay */}
      <div className="zone-canvas-wrap">
        {snapError ? (
          <div className="zone-snap-placeholder">
            <span>📷 Snapshot belum tersedia</span>
            <small>Jalankan analyzer service terlebih dahulu</small>
          </div>
        ) : (
          <img
            ref={imgRef}
            src={snapUrl}
            alt={camera.name}
            className="zone-snap-img"
            onLoad={onImgLoad}
            onError={() => setSnapError(true)}
          />
        )}
        {!snapError && imgSize && (
          <ZoneCanvas
            imgSize={imgSize}
            zones={zones}
            draftPoints={draftPoints}
            onCanvasClick={handleCanvasClick}
            selectedZoneId={selectedZone}
          />
        )}
      </div>

      {/* Draft controls */}
      {draftPoints.length > 0 && (
        <div className="zone-draft-bar">
          <input
            className="zone-name-input"
            placeholder="Nama zona (e.g. Pintu Depan)"
            value={draftName}
            onChange={e => setDraftName(e.target.value)}
          />
          <span className="zone-pts-count">{draftPoints.length} titik</span>
          <button
            className="btn btn-primary"
            disabled={draftPoints.length < 3 || saving}
            onClick={handleSaveZone}
          >
            {saving ? 'Menyimpan…' : '💾 Simpan Zona'}
          </button>
          <button className="btn btn-ghost" onClick={() => setDraftPoints([])}>
            ✕ Batal
          </button>
        </div>
      )}

      {draftPoints.length === 0 && (
        <p className="zone-hint">
          Klik pada gambar untuk menambah titik zona. Minimal 3 titik untuk menyimpan.
        </p>
      )}

      {/* Saved zones list */}
      {zones.length > 0 && (
        <div className="zone-list">
          {zones.map((zone, zi) => (
            <div
              key={zone.id}
              className={`zone-list-row${selectedZone === zone.id ? ' selected' : ''}`}
              onClick={() => setSelectedZone(selectedZone === zone.id ? null : zone.id)}
            >
              <span
                className="zone-color-dot"
                style={{ background: COLORS[zi % COLORS.length] }}
              />
              <span className="zone-list-name">{zone.name}</span>
              <button
                className={`zone-toggle-btn ${zone.enabled ? 'tog-on' : 'tog-off'}`}
                onClick={e => { e.stopPropagation(); handleToggleZone(zone) }}
                title={zone.enabled ? 'Nonaktifkan' : 'Aktifkan'}
              >
                {zone.enabled ? 'Aktif' : 'Nonaktif'}
              </button>
              <button
                className="btn-icon"
                onClick={e => { e.stopPropagation(); handleDeleteZone(zone.id) }}
                title="Hapus zona"
              >
                🗑
              </button>
            </div>
          ))}
        </div>
      )}

      {zones.length === 0 && draftPoints.length === 0 && (
        <p className="zone-empty">Belum ada zona. Klik pada gambar untuk mulai menggambar.</p>
      )}
    </div>
  )
}
