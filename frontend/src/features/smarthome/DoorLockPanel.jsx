import React, { useEffect, useState, useRef, useCallback } from 'react'
import {
  fetchDoorlockStatus,
  doorlockUnlock,
  doorlockLock,
  doorlockCameraStream,
  doorlockCameraStop,
  doorlockTalkStart,
  doorlockTalkStop,
  fetchDoorlockAlerts,
} from '../../services/api'

export default function DoorLockPanel() {
  const [status, setStatus]       = useState(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading]     = useState(true)
  const [actionMsg, setActionMsg] = useState(null)
  const [streamUrl, setStreamUrl] = useState(null)
  const [streaming, setStreaming] = useState(false)
  const [talking, setTalking]     = useState(false)
  const [alerts, setAlerts]       = useState([])
  const [showAlerts, setShowAlerts] = useState(false)
  const videoRef = useRef(null)
  const refreshTimer = useRef(null)

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchDoorlockStatus()
      setConnected(data.connected || false)
      setStatus(data.status || null)
    } catch {
      setConnected(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStatus()
    refreshTimer.current = setInterval(loadStatus, 15000)
    return () => clearInterval(refreshTimer.current)
  }, [loadStatus])

  useEffect(() => {
    if (showAlerts) {
      fetchDoorlockAlerts(30)
        .then(data => setAlerts(data.alerts || []))
        .catch(() => {})
    }
  }, [showAlerts])

  async function handleUnlock() {
    setActionMsg(null)
    try {
      await doorlockUnlock()
      setActionMsg({ ok: true, text: '🔓 Pintu dibuka!' })
      setTimeout(loadStatus, 2000)
    } catch (err) {
      setActionMsg({ ok: false, text: err.message })
    }
  }

  async function handleLock() {
    setActionMsg(null)
    try {
      await doorlockLock()
      setActionMsg({ ok: true, text: '🔒 Pintu dikunci!' })
      setTimeout(loadStatus, 2000)
    } catch (err) {
      setActionMsg({ ok: false, text: err.message })
    }
  }

  async function handleStartStream() {
    setActionMsg(null)
    try {
      const data = await doorlockCameraStream()
      if (data.ok && data.stream?.url) {
        setStreamUrl(data.stream.url)
        setStreaming(true)
      } else {
        setActionMsg({ ok: false, text: 'Stream URL tidak tersedia' })
      }
    } catch (err) {
      setActionMsg({ ok: false, text: err.message })
    }
  }

  async function handleStopStream() {
    try {
      await doorlockCameraStop()
    } catch { /* ignore */ }
    setStreamUrl(null)
    setStreaming(false)
  }

  async function handleStartTalk() {
    setActionMsg(null)
    try {
      await doorlockTalkStart()
      setTalking(true)
      setActionMsg({ ok: true, text: '🎙 Intercom aktif' })
    } catch (err) {
      setActionMsg({ ok: false, text: err.message })
    }
  }

  async function handleStopTalk() {
    try {
      await doorlockTalkStop()
    } catch { /* ignore */ }
    setTalking(false)
  }

  if (loading) {
    return (
      <div className="doorlock-panel">
        <div className="doorlock-header">🚪 Smart Door Lock</div>
        <div className="doorlock-loading">Memuat…</div>
      </div>
    )
  }

  if (!connected) {
    return (
      <div className="doorlock-panel">
        <div className="doorlock-header">🚪 Smart Door Lock</div>
        <div className="doorlock-disconnected">
          Tidak terhubung. Konfigurasi di halaman ⚙ Konfigurasi → 🚪 Door Lock.
        </div>
      </div>
    )
  }

  const batteryLevel = status?.residual_electricity ?? status?.battery_state ?? null
  const lockState = status?.closed_opened ?? status?.lock_motor_state ?? null

  return (
    <div className="doorlock-panel">
      <div className="doorlock-header">
        🚪 Paloma DLP6202
        <span className="doorlock-status-badge online">● Online</span>
      </div>

      {/* Status indicators */}
      <div className="doorlock-status-row">
        {batteryLevel !== null && (
          <div className="doorlock-stat">
            🔋 {typeof batteryLevel === 'number' ? `${batteryLevel}%` : batteryLevel}
          </div>
        )}
        <div className="doorlock-stat">
          {lockState === true || lockState === 'opened'
            ? '🔓 Terbuka'
            : '🔒 Terkunci'}
        </div>
      </div>

      {/* Action buttons */}
      <div className="doorlock-actions">
        <button className="btn doorlock-btn unlock" onClick={handleUnlock}>
          🔓 Buka Pintu
        </button>
        <button className="btn doorlock-btn lock" onClick={handleLock}>
          🔒 Kunci Pintu
        </button>
      </div>

      {/* Camera stream */}
      <div className="doorlock-camera-section">
        {!streaming ? (
          <button className="btn doorlock-btn camera" onClick={handleStartStream}>
            📷 Lihat Kamera
          </button>
        ) : (
          <div className="doorlock-camera-view">
            <video
              ref={videoRef}
              src={streamUrl}
              autoPlay
              playsInline
              muted
              style={{ width: '100%', borderRadius: 8 }}
            />
            <button className="btn doorlock-btn stop" onClick={handleStopStream}>
              ⏹ Tutup Kamera
            </button>
          </div>
        )}
      </div>

      {/* Talk/Intercom */}
      <div className="doorlock-talk-section">
        {!talking ? (
          <button className="btn doorlock-btn talk" onClick={handleStartTalk}>
            🎙 Bicara (Intercom)
          </button>
        ) : (
          <button className="btn doorlock-btn stop-talk" onClick={handleStopTalk}>
            🔇 Hentikan Intercom
          </button>
        )}
      </div>

      {/* Action message */}
      {actionMsg && (
        <div className={`doorlock-msg ${actionMsg.ok ? 'ok' : 'err'}`}>
          {actionMsg.text}
        </div>
      )}

      {/* Alerts toggle */}
      <div className="doorlock-alerts-section">
        <button
          className="btn btn-ghost doorlock-alerts-toggle"
          onClick={() => setShowAlerts(v => !v)}
        >
          🔔 {showAlerts ? 'Sembunyikan' : 'Lihat'} Riwayat Alert
        </button>

        {showAlerts && (
          <div className="doorlock-alerts-list">
            {alerts.length === 0 ? (
              <div className="doorlock-alert-empty">Belum ada alert.</div>
            ) : (
              alerts.map((alert, idx) => (
                <div key={idx} className="doorlock-alert-item">
                  <span className="doorlock-alert-time">
                    {alert.ts || alert.event_time || '—'}
                  </span>
                  <span className="doorlock-alert-type">
                    {alert.type || alert.event_type || alert.code || 'event'}
                  </span>
                  <span className="doorlock-alert-detail">
                    {alert.detail || alert.value || ''}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}
