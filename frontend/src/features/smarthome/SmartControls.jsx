import React, { useState } from 'react'
import NVREventLog from './NVREventLog'
import HomeAwayToggle from './HomeAwayToggle'

const DEFAULT_LIGHTS = [
  { id: 1, name: 'Lampu Depan',      icon: '💡', on: false },
  { id: 2, name: 'Lampu Taman',      icon: '🔦', on: false },
  { id: 3, name: 'Lampu Garasi',     icon: '💡', on: false },
  { id: 4, name: 'Lampu Ruang Tamu', icon: '💡', on: false },
]

function loadLights() {
  try { return JSON.parse(localStorage.getItem('hd_lights')) || DEFAULT_LIGHTS }
  catch { return DEFAULT_LIGHTS }
}

function loadGate() {
  const v = localStorage.getItem('hd_gate')
  return v === 'open' ? 'open' : 'closed'
}

export default function SmartControls({ mode = 'home', modeLoading = false, onModeChange }) {
  const [lights, setLights] = useState(loadLights)
  const [gate, setGate]     = useState(loadGate)
  const [gateMoving, setGateMoving] = useState(false)

  const toggleLight = (id) => {
    setLights(prev => {
      const next = prev.map(l => l.id === id ? { ...l, on: !l.on } : l)
      localStorage.setItem('hd_lights', JSON.stringify(next))
      return next
    })
  }

  const toggleGate = () => {
    if (gateMoving) return
    setGateMoving(true)
    setTimeout(() => {
      setGate(prev => {
        const next = prev === 'closed' ? 'open' : 'closed'
        localStorage.setItem('hd_gate', next)
        return next
      })
      setGateMoving(false)
    }, 3000)
  }

  return (
    <>
      <HomeAwayToggle mode={mode} onChange={onModeChange} loading={modeLoading} />
      <div className="controls-grid">
      {lights.map(light => (
        <button
          key={light.id}
          className={`control-card${light.on ? ' active' : ''}`}
          onClick={() => toggleLight(light.id)}
          title={light.on ? 'Klik untuk matikan' : 'Klik untuk nyalakan'}
        >
          <span className="control-icon">{light.on ? '💡' : '🔌'}</span>
          <span className="control-name">{light.name}</span>
          <span className={`control-badge ${light.on ? 'badge-on' : 'badge-off'}`}>
            {light.on ? 'ON' : 'OFF'}
          </span>
        </button>
      ))}

      <button
        className={`control-card green${gate === 'open' ? ' active' : ''}${gateMoving ? ' moving' : ''}`}
        onClick={toggleGate}
        title={gateMoving ? 'Sedang bergerak...' : gate === 'open' ? 'Tutup gate' : 'Buka gate'}
      >
        <span className="control-icon">
          {gateMoving ? '⚙️' : gate === 'open' ? '🔓' : '🔒'}
        </span>
        <span className="control-name">Gate Utama</span>
        <span className={`control-badge ${gateMoving ? 'badge-moving' : gate === 'open' ? 'badge-open' : 'badge-closed'}`}>
          {gateMoving ? 'BERGERAK...' : gate === 'open' ? 'TERBUKA' : 'TERTUTUP'}
        </span>
      </button>
    </div>
    <NVREventLog />
    </>
  )
}
