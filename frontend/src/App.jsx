import { useState } from 'react'
import Dashboard from './components/Dashboard'
import Files from './components/Files'
import Workflows from './components/Workflows'
import Configure from './components/Configure'

const TABS = ['Dashboard', 'Files', 'Workflows', 'Configure']

export default function App() {
  const [tab, setTab]         = useState('Dashboard')
  const [workflow, setWorkflow] = useState('Ingest')

  const goWorkflow = (name) => { setWorkflow(name); setTab('Workflows') }

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
        {tab === 'Dashboard' && <Dashboard onGoScan={() => goWorkflow('Scan')} />}
        {tab === 'Files'     && <Files />}
        {tab === 'Workflows' && <Workflows active={workflow} onSelect={setWorkflow} onConfigure={() => setTab('Configure')} />}
        {tab === 'Configure' && <Configure />}
      </main>
    </div>
  )
}
