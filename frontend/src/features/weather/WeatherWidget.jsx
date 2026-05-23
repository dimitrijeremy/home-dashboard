import React, { useState, useEffect } from 'react'

// WMO Weather Code → {label, icon}
const WMO = {
  0:  ['Cerah',              '☀️'],
  1:  ['Cerah Berawan',      '🌤️'],
  2:  ['Partly Cloudy',      '⛅'],
  3:  ['Mendung',            '☁️'],
  45: ['Berkabut',           '🌫️'],
  48: ['Kabut Beku',         '🌫️'],
  51: ['Gerimis Tipis',      '🌦️'],
  53: ['Gerimis',            '🌦️'],
  55: ['Gerimis Lebat',      '🌧️'],
  61: ['Hujan Ringan',       '🌧️'],
  63: ['Hujan',              '🌧️'],
  65: ['Hujan Lebat',        '🌧️'],
  71: ['Salju Ringan',       '🌨️'],
  73: ['Salju',              '🌨️'],
  75: ['Salju Lebat',        '❄️'],
  80: ['Hujan Lokal',        '🌦️'],
  81: ['Hujan Lokal Sedang', '🌧️'],
  82: ['Hujan Lokal Lebat',  '⛈️'],
  95: ['Badai Petir',        '⛈️'],
  96: ['Badai + Hujan Es',   '⛈️'],
  99: ['Badai Besar',        '⛈️'],
}

// Default: Jakarta
const DEFAULT_LAT  = -6.2088
const DEFAULT_LON  = 106.8456
const DEFAULT_CITY = 'Jakarta'

function wmoInfo(code) {
  const entry = WMO[code]
  if (entry) return { label: entry[0], icon: entry[1] }
  if (code >= 51 && code <= 67) return { label: 'Hujan',    icon: '🌧️' }
  if (code >= 71 && code <= 77) return { label: 'Salju',    icon: '🌨️' }
  if (code >= 80 && code <= 99) return { label: 'Hujan',    icon: '⛈️' }
  return { label: 'Tidak diketahui', icon: '🌡️' }
}

function windDir(deg) {
  const dirs = ['U','TL','T','TG','S','BD','B','BL']
  return dirs[Math.round(deg / 45) % 8]
}

export default function WeatherWidget() {
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  useEffect(() => {
    const lat  = DEFAULT_LAT
    const lon  = DEFAULT_LON
    const url  = `https://api.open-meteo.com/v1/forecast`
                + `?latitude=${lat}&longitude=${lon}`
                + `&current_weather=true`
                + `&hourly=relativehumidity_2m,apparent_temperature`
                + `&timezone=Asia%2FJakarta`
                + `&forecast_days=1`

    fetch(url, { signal: AbortSignal.timeout(8000) })
      .then(r => { if (!r.ok) throw new Error('fetch failed'); return r.json() })
      .then(json => {
        const cw  = json.current_weather
        const now = new Date()
        const hour = now.getHours()
        setData({
          temp:      Math.round(cw.temperature),
          windspeed: Math.round(cw.windspeed),
          winddir:   windDir(cw.winddirection),
          code:      cw.weathercode,
          humidity:  json.hourly?.relativehumidity_2m?.[hour] ?? '–',
          feels:     Math.round(json.hourly?.apparent_temperature?.[hour] ?? cw.temperature),
        })
        setLoading(false)
      })
      .catch(e => {
        setError('Gagal memuat cuaca')
        setLoading(false)
      })
  }, [])

  if (loading) return (
    <div className="weather-card weather-loading">⏳ Memuat cuaca...</div>
  )
  if (error || !data) return (
    <div className="weather-card weather-loading">❌ {error || 'Cuaca tidak tersedia'}</div>
  )

  const { label, icon } = wmoInfo(data.code)

  return (
    <div className="weather-card">
      <div className="weather-icon">{icon}</div>
      <div className="weather-info">
        <div className="weather-temp">
          {data.temp}<sup>°C</sup>
        </div>
        <div className="weather-desc">{label}</div>
        <div className="weather-location">
          📍 {DEFAULT_CITY} · Terasa {data.feels}°C
        </div>
      </div>
      <div className="weather-details">
        <span>💧 {data.humidity}%</span>
        <span>💨 {data.windspeed} km/h {data.winddir}</span>
      </div>
    </div>
  )
}
