import React, { useState } from 'react'
import { login } from '../services/api'

export default function Login({ onLoggedIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!username.trim() || !password) { setError('Username dan password wajib diisi'); return }
    setLoading(true)
    setError('')
    try {
      const res = await login(username.trim(), password)
      onLoggedIn(res.username)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <form className="modal login-card" onSubmit={submit}>
        <div className="modal-title">🔒 Login Dashboard</div>

        <label>Username</label>
        <input
          autoFocus
          value={username}
          onChange={e => { setUsername(e.target.value); setError('') }}
          autoComplete="username"
        />

        <label>Password</label>
        <input
          type="password"
          value={password}
          onChange={e => { setPassword(e.target.value); setError('') }}
          autoComplete="current-password"
        />

        {error && (
          <div style={{ color: 'var(--red)', fontSize: '.78rem', marginTop: 10 }}>⚠ {error}</div>
        )}

        <div className="modal-actions" style={{ justifyContent: 'stretch', marginTop: 20 }}>
          <button type="submit" className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }} disabled={loading}>
            {loading ? 'Masuk…' : 'Masuk'}
          </button>
        </div>
      </form>
    </div>
  )
}
