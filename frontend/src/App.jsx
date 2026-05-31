import React, { useState } from 'react'
import Dashboard from './pages/Dashboard'
import ConfigPage from './pages/ConfigPage'

export default function App() {
  const [page, setPage] = useState('dashboard')
  if (page === 'config') return <ConfigPage onBack={() => setPage('dashboard')} />
  return <Dashboard onConfig={() => setPage('config')} />
}
