import React from 'react'

/**
 * HomeAwayToggle — controlled component.
 *
 * Props:
 *   mode        'home' | 'away'
 *   onChange    (newMode) => void  — called after successful API update
 *   loading     bool               — show spinner while API call is in flight
 */
export default function HomeAwayToggle({ mode = 'home', onChange, loading = false }) {
  const isAway = mode === 'away'

  const handleClick = (newMode) => {
    if (newMode !== mode && !loading) onChange?.(newMode)
  }

  return (
    <div className="ha-wrap">
      {/* Banner shown only in away mode */}
      {isAway && (
        <div className="ha-away-banner">
          <span className="ha-banner-icon">🚨</span>
          <span>Mode <strong>PERGI</strong> aktif — deteksi manusia akan memicu alarm</span>
        </div>
      )}

      <div className="ha-section-label">Status Hunian</div>

      <div className={`ha-toggle${loading ? ' ha-loading' : ''}`}>
        <button
          className={`ha-btn ha-home${!isAway ? ' active' : ''}`}
          onClick={() => handleClick('home')}
          disabled={loading}
          title="Mode di rumah — alarm nonaktif"
        >
          <span className="ha-icon">🏠</span>
          <span className="ha-label">Di Rumah</span>
          {!isAway && !loading && <span className="ha-active-dot" />}
        </button>

        <button
          className={`ha-btn ha-away${isAway ? ' active' : ''}`}
          onClick={() => handleClick('away')}
          disabled={loading}
          title="Mode pergi — deteksi akan memicu alarm"
        >
          <span className="ha-icon">🚨</span>
          <span className="ha-label">Pergi</span>
          {isAway && !loading && <span className="ha-active-dot" />}
        </button>
      </div>
    </div>
  )
}
