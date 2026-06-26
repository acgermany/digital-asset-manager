import { useState } from 'react'
import Dashboard from './components/Dashboard'
import Duplicates from './components/Duplicates'
import Files from './components/Files'
import ScanControl from './components/ScanControl'

const TABS = ['Dashboard', 'Files', 'Duplicates', 'Scan']

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
      </header>

      <main className="main">
        {tab === 'Dashboard'  && <Dashboard onGoScan={() => setTab('Scan')} />}
        {tab === 'Files'      && <Files />}
        {tab === 'Duplicates' && <Duplicates />}
        {tab === 'Scan'       && <ScanControl />}
      </main>
    </div>
  )
}
