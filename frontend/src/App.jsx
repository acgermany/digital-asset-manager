import { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import Login from './components/Login'
import { subscribe as subscribeUploads, summary as uploadSummary } from './uploads'
import Dashboard from './components/Dashboard'
import Files from './components/Files'
import Workflows from './components/Workflows'
import Configure from './components/Configure'

const TABS = ['Dashboard', 'Files', 'Workflows', 'Configure']

export default function App() {
  const [tab, setTab]         = useState('Dashboard')
  const [workflow, setWorkflow] = useState('Ingest')

  const [auth, setAuth]         = useState(null)   // null = checking, 'ok' | 'login' | 'setup'
  const [uploads, setUploads]   = useState({ active: 0, pct: 0 })
  useEffect(() => subscribeUploads(list => setUploads(uploadSummary(list))), [])

  const checkAuth = useCallback(async () => {
    try {
      const s = await api.authStatus()
      setAuth(!s.configured ? 'setup' : s.authenticated ? 'ok' : 'login')
    } catch {
      setAuth('login')
    }
  }, [])

  useEffect(() => {
    checkAuth()
    const onAuth = e => setAuth(e.detail === 'setup' ? 'setup' : 'login')
    window.addEventListener('dam-auth', onAuth)
    return () => window.removeEventListener('dam-auth', onAuth)
  }, [checkAuth])

  const goWorkflow = (name) => { setWorkflow(name); setTab('Workflows') }
  const signOut = async () => { try { await api.logout() } finally { setAuth('login') } }

  if (auth === null) return null
  if (auth !== 'ok') return <Login mode={auth} onDone={checkAuth} />

  return (
    <div className="layout">
      <header className="topbar">
        <span className="topbar-logo">DAM<span>·</span>nas</span>
        <nav className="nav">
          {TABS.map(t => (
            <button
              key={t}
              className={`nav-btn${tab === t ? ' active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <span style={{ flex: 1 }} />
        {uploads.active > 0 && (
          <button className="nav-btn upload-indicator" onClick={() => goWorkflow('Ingest')} title="Uploads in progress">
            ⬆ {uploads.active} uploading · {uploads.pct}%
          </button>
        )}
        <button className="nav-btn" onClick={signOut} title="Sign out">Sign out</button>
      </header>

      <main className="main">
        {tab === 'Dashboard' && <Dashboard onGoScan={() => goWorkflow('Scan')} />}
        {tab === 'Files'     && <Files />}
        {tab === 'Workflows' && <Workflows active={workflow} onSelect={setWorkflow} onConfigure={() => setTab('Configure')} />}
        {tab === 'Configure' && <Configure />}
      </main>
    </div>
  )
}
