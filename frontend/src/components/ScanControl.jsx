import { useState, useEffect } from 'react'
import { api } from '../api'
import FolderPicker from './FolderPicker'

export default function ScanControl() {
  const [status, setStatus]   = useState(null)
  const [scanPath, setScanPath] = useState('')
  const [workers, setWorkers]  = useState(2)
  const [err, setErr]          = useState(null)
  const [picking, setPicking]  = useState(false)
  const [sources, setSources]  = useState([])
  const [serverName, setServerName] = useState(null)

  useEffect(() => {
    api.config().then(c => {
      const list = c.settings.catalog_sources || []
      setServerName(c.server_name)
      setSources(list)
      setScanPath(p => p || list[0] || '')
    }).catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const s = await api.scanStatus()
        if (alive) { setStatus(s); setErr(null) }
      } catch (e) {
        if (alive) setErr(e.message)
      }
    }
    poll()
    const t = setInterval(poll, 1000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const handleStart = async () => {
    try {
      setErr(null)
      await api.startScan(scanPath, workers)
    } catch (e) {
      setErr(e.message)
    }
  }

  const handleCancel = async () => {
    try {
      setErr(null)
      await api.cancelScan()
    } catch (e) {
      setErr(e.message)
    }
  }

  const isStopping = status?.phase === 'stopping'

  const pct = status?.total
    ? Math.round(status.progress / status.total * 100)
    : 0

  const duration = status?.start_time && status?.end_time
    ? Math.round((new Date(status.end_time) - new Date(status.start_time)) / 1000)
    : null

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Scan Control</div>
          <div className="section-sub">Inventory your NAS and compute checksums</div>
        </div>
      </div>

      {err && <div className="error-box">{err}</div>}

      {/* Config */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title" style={{ marginBottom: 12 }}>Scan configuration</div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              Folder to scan
            </label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                className="search"
                style={{ width: 360 }}
                value={scanPath}
                onChange={e => setScanPath(e.target.value)}
                disabled={status?.running}
                placeholder="Choose a folder…"
              />
              <button className="btn sm" onClick={() => setPicking(true)} disabled={status?.running}>
                Browse
              </button>
            </div>
            {sources.length > 0 && (
              <div className="chip-row">
                {sources.map(src => (
                  <button key={src} className="chip" disabled={status?.running} onClick={() => setScanPath(src)}>
                    {src}
                  </button>
                ))}
              </div>
            )}
            {picking && (
              <FolderPicker
                title="Choose a folder to scan"
                serverName={serverName}
                initialPath={scanPath || null}
                onClose={() => setPicking(false)}
                onSelect={p => { setScanPath(p); setPicking(false) }}
              />
            )}
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              Worker threads
            </label>
            <input
              className="search"
              style={{ width: 80 }}
              type="number"
              min={1}
              max={16}
              value={workers}
              onChange={e => setWorkers(Number(e.target.value))}
              disabled={status?.running}
            />
          </div>
          <button
            className="btn primary"
            onClick={handleStart}
            disabled={status?.running}
          >
            Start Scan
          </button>
          {status?.running && (
            <button
              className="btn"
              onClick={handleCancel}
              disabled={isStopping}
              style={{ color: isStopping ? 'var(--text-muted)' : 'var(--red)' }}
            >
              {isStopping ? 'Stopping…' : 'Stop'}
            </button>
          )}
        </div>
      </div>

      {/* Status */}
      {status && (
        <div className="scan-grid">
          <div className="card">
            <div className="card-title">Status</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span className={`pill ${status.running ? 'green' : status.error ? 'red' : 'grey'}`} />
              <span style={{ fontWeight: 600 }}>
                {status.running ? 'Running' : status.error ? 'Error' : status.cancelled ? 'Cancelled' : status.end_time ? 'Completed' : 'Idle'}
              </span>
            </div>
            {status.scan_path && status.running && (
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, fontFamily: 'monospace' }}>
                {status.scan_path}
              </div>
            )}
            {status.running && status.phase === 'discovering' && (
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
                <span className="spinner" style={{ marginRight: 6 }} />
                Discovering files… {status.total > 0 && `${status.total.toLocaleString()} found so far`}
              </div>
            )}
            {status.running && status.phase === 'scanning' && (
              <>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${pct}%` }} />
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                  {pct}% · {status.progress.toLocaleString()} of {status.total.toLocaleString()} files
                </div>
                <div className="scan-file">{status.current_file}</div>
              </>
            )}
            {status.running && status.phase === 'stopping' && (
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
                <span className="spinner" style={{ marginRight: 6 }} />
                Stopping… finishing in-progress checksums
              </div>
            )}
            {status.error && (
              <div style={{ fontSize: 13, color: 'var(--red)', marginTop: 4 }}>{status.error}</div>
            )}
          </div>

          <div className="card">
            <div className="card-title">Last run</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Files processed</div>
                <div style={{ fontWeight: 700, fontSize: 20 }}>{status.progress.toLocaleString()}</div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Errors</div>
                <div style={{ fontWeight: 700, fontSize: 20, color: status.errors > 0 ? 'var(--red)' : undefined }}>
                  {status.errors}
                </div>
              </div>
              {status.start_time && (
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Started</div>
                  <div style={{ fontSize: 13 }}>{new Date(status.start_time).toLocaleTimeString()}</div>
                </div>
              )}
              {duration !== null && (
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Duration</div>
                  <div style={{ fontSize: 13 }}>{duration}s</div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="card" style={{ marginTop: 8 }}>
        <div className="card-title" style={{ marginBottom: 8 }}>Notes</div>
        <ul style={{ paddingLeft: 18, color: 'var(--text-muted)', fontSize: 13, lineHeight: 2 }}>
          <li>The scan path inside the container is <code style={{ background: 'var(--surface2)', padding: '1px 6px', borderRadius: 4 }}>/scan</code>, mounted from your NAS via docker-compose.</li>
          <li>SHA-256 checksums are computed for every file — large libraries take time on first scan.</li>
          <li>Subsequent scans replace existing records for the same file.</li>
          <li>After scanning, go to <strong>Duplicates</strong> to review and mark files.</li>
        </ul>
      </div>
    </>
  )
}
