import { useState } from 'react'
import Dashboard from './components/Dashboard'
import Duplicates from './components/Duplicates'
import Files from './components/Files'
import Ingest from './components/Ingest'
import Jobs from './components/Jobs'
import ScanControl from './components/ScanControl'

const TABS = ['Dashboard', 'Ingest', 'Jobs', 'Files', 'Duplicates', 'Scan']

export default function App() {
  const [tab, setTab] = useState('Dashboard')

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
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto', letterSpacing: '0.04em' }}>
          {import.meta.env.VITE_APP_VERSION || 'dev'}
        </span>
      </header>

      <main className="main">
        {tab === 'Dashboard'  && <Dashboard onGoScan={() => setTab('Scan')} />}
        {tab === 'Ingest'     && <Ingest />}
        {tab === 'Jobs'       && <Jobs />}
        {tab === 'Files'      && <Files />}
        {tab === 'Duplicates' && <Duplicates />}
        {tab === 'Scan'       && <ScanControl />}
      </main>
    </div>
  )
}
