import React, { useEffect, useState } from 'react'
import Dashboard from './pages/Dashboard'
import ConfigPage from './pages/ConfigPage'
import Login from './pages/Login'
import { fetchSession, logout, setUnauthorizedHandler } from './services/api'

// Cek proaktif setiap 60s — supaya user yang cuma nonton stream (tanpa
// interaksi API lain) tetap ke-redirect ke login saat sesi idle timeout,
// bukan cuma nunggu request berikutnya gagal.
const SESSION_POLL_MS = 60_000

export default function App() {
  const [page, setPage] = useState('dashboard')
  const [authChecked, setAuthChecked] = useState(false)
  const [authed, setAuthed] = useState(false)

  useEffect(() => {
    setUnauthorizedHandler(() => setAuthed(false))
    fetchSession()
      .then(s => setAuthed(s.authenticated))
      .catch(() => setAuthed(false))
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    if (!authed) return
    const id = setInterval(() => {
      fetchSession().then(s => { if (!s.authenticated) setAuthed(false) }).catch(() => {})
    }, SESSION_POLL_MS)
    return () => clearInterval(id)
  }, [authed])

  const handleLogout = async () => {
    try { await logout() } catch { /* ignore */ }
    setAuthed(false)
    setPage('dashboard')
  }

  if (!authChecked) return null
  if (!authed) return <Login onLoggedIn={() => setAuthed(true)} />
  if (page === 'config') return <ConfigPage onBack={() => setPage('dashboard')} onLogout={handleLogout} />
  return <Dashboard onConfig={() => setPage('config')} onLogout={handleLogout} />
}
