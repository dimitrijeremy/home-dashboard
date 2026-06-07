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
const WEATHER_STORAGE_KEY = 'hd_weather_city'
const LOCATIONS = [
  { id: 'jakarta',   label: 'Jakarta',   lat: -6.2088,  lon: 106.8456 },
  { id: 'bandung',   label: 'Bandung',   lat: -6.9175,  lon: 107.6191 },
  { id: 'surabaya',  label: 'Surabaya',  lat: -7.2575,  lon: 112.7521 },
  { id: 'denpasar',  label: 'Denpasar',  lat: -8.6500,  lon: 115.2167 },
  { id: 'medan',     label: 'Medan',     lat: 3.5952,   lon: 98.6722 },
  { id: 'makassar',  label: 'Makassar',  lat: -5.1477,  lon: 119.4327 },
]

function loadLocationId() {
  if (typeof window === 'undefined') return LOCATIONS[0].id
  const saved = localStorage.getItem(WEATHER_STORAGE_KEY)
  return LOCATIONS.some(item => item.id === saved) ? saved : LOCATIONS[0].id
}

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
  const [locationId, setLocationId] = useState(loadLocationId)
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState(null)

  const location = LOCATIONS.find(item => item.id === locationId) ?? LOCATIONS[0]

  useEffect(() => {
    const controller = new AbortController()
    const lat  = location.lat
    const lon  = location.lon
    const url  = `https://api.open-meteo.com/v1/forecast`
                + `?latitude=${lat}&longitude=${lon}`
                + `&current_weather=true`
                + `&hourly=relativehumidity_2m,apparent_temperature`
                + `&timezone=Asia%2FJakarta`
                + `&forecast_days=1`

    setLoading(true)
    setError(null)

    fetch(url, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) })
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
        if (e.name === 'AbortError') return
        setError('Gagal memuat cuaca')
        setLoading(false)
      })

    return () => controller.abort()
  }, [location.id, location.lat, location.lon])

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem(WEATHER_STORAGE_KEY, locationId)
  }, [locationId])

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
        <div className="weather-select-row">
          <label htmlFor="weather-city" className="weather-select-label">Lokasi</label>
          <select
            id="weather-city"
            className="weather-location-select"
            value={locationId}
            onChange={(event) => setLocationId(event.target.value)}
          >
            {LOCATIONS.map((item) => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </div>
        <div className="weather-temp">
          {data.temp}<sup>°C</sup>
        </div>
        <div className="weather-desc">{label}</div>
        <div className="weather-location">
          📍 {location.label} · Terasa {data.feels}°C
        </div>
      </div>
      <div className="weather-details">
        <span>💧 {data.humidity}%</span>
        <span>💨 {data.windspeed} km/h {data.winddir}</span>
      </div>
    </div>
  )
}
