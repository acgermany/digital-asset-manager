import { useState, useEffect, useRef } from 'react'
import { api } from '../api'

const DEFAULT_PATH = import.meta.env.VITE_SCAN_PATH || '/nas/ingest'

export default function ScanControl() {
  const [activeJob, setActiveJob] = useState(null)
  const [lastJob, setLastJob]     = useState(null)
  const [scanPath, setScanPath]   = useState(DEFAULT_PATH)
  const [workers, setWorkers]     = useState(4)
  const [err, setErr]             = useState(null)
  const [browser, setBrowser]     = useState(null)
  const [browseErr, setBrowseErr] = useState(null)
  const browserRef = useRef(null)

  useEffect(() => {
    if (!browser) return
    function onDown(e) {
      if (browserRef.current && !browserRef.current.contains(e.target)) {
        setBrowser(null)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [browser])

  const fetchLastJob = async () => {
    try {
      const data = await api.recentJobs(1)
      const jobs = data?.jobs || []
      const done = jobs.find(j => j.status === 'completed' || j.status === 'cancelled' || j.status === 'failed')
      if (done) setLastJob(done)
    } catch (_) {}
  }

  useEffect(() => {
    fetchLastJob()
  }, [])

  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const job = await api.activeJob()
        if (!alive) return
        setActiveJob(job)
        setErr(null)
        if (!job) {
          fetchLastJob()
        }
      } catch (e) {
        if (alive) setErr(e.message)
      }
    }
    poll()
    const t = setInterval(poll, 1000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const openBrowser = async () => {
    setBrowseErr(null)
    try {
      const data = await api.browse(scanPath || DEFAULT_PATH)
      if (data.error) { setBrowseErr(data.error); return }
      setBrowser(data)
    } catch (e) {
      setBrowseErr(e.message)
    }
  }

  const browseInto = async (path) => {
    try {
      const data = await api.browse(path)
      if (data.error) { setBrowseErr(data.error); return }
      setBrowser(data)
    } catch (e) {
      setBrowseErr(e.message)
    }
  }

  const selectDir = (path) => {
    setScanPath(path)
    setBrowser(null)
  }

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
      await api.cancelJob(activeJob.id)
    } catch (e) {
      setErr(e.message)
    }
  }

  const isRunning   = activeJob?.status === 'running'
  const isPending   = activeJob?.status === 'pending'
  const isCancelling = activeJob?.status === 'cancelling'
  const isActive    = isRunning || isPending || isCancelling

  const pct = activeJob?.total
    ? Math.round(activeJob.progress / activeJob.total * 100)
    : 0

  const displayJob = activeJob || lastJob

  const duration = displayJob?.started_at && displayJob?.completed_at
    ? Math.round((new Date(displayJob.completed_at) - new Date(displayJob.started_at)) / 1000)
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
          <div style={{ position: 'relative' }} ref={browserRef}>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
              Directory path
            </label>
            <div style={{ display: 'flex', gap: 6 }}>
              <input
                className="search"
                style={{ width: 300 }}
                value={scanPath}
                onChange={e => setScanPath(e.target.value)}
                disabled={isActive}
                placeholder="/scan"
              />
              <button
                className="btn sm"
                onClick={openBrowser}
                disabled={isActive}
                title="Browse directories"
              >
                Browse
              </button>
            </div>

            {browseErr && (
              <div style={{ fontSize: 12, color: 'var(--red)', marginTop: 4 }}>{browseErr}</div>
            )}

            {browser && (
              <div className="dir-browser">
                <div className="dir-browser-path">
                  {browser.parent && (
                    <button className="btn sm" style={{ marginRight: 8 }} onClick={() => browseInto(browser.parent)}>
                      ↑ Up
                    </button>
                  )}
                  <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-muted)' }}>
                    {browser.path}
                  </span>
                </div>
                <div className="dir-browser-list">
                  <div
                    className="dir-item dir-item-select"
                    onClick={() => selectDir(browser.path)}
                  >
                    <span style={{ color: 'var(--accent-lt)' }}>✓</span> Use this directory
                  </div>
                  {browser.dirs.length === 0 && (
                    <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text-muted)' }}>
                      No subdirectories
                    </div>
                  )}
                  {browser.dirs.map(d => (
                    <div key={d.path} className="dir-item" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <button
                        className="btn sm"
                        onClick={() => selectDir(d.path)}
                        title="Use this directory"
                        style={{ flexShrink: 0 }}
                      >
                        ✓
                      </button>
                      <span
                        style={{ flex: 1, cursor: 'pointer', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        onClick={() => browseInto(d.path)}
                        title="Browse into this directory"
                      >
                        <span style={{ marginRight: 6, color: 'var(--text-muted)' }}>📁</span>
                        {d.name}
                      </span>
                      <span
                        style={{ flexShrink: 0, color: 'var(--text-muted)', cursor: 'pointer', padding: '0 4px' }}
                        onClick={() => browseInto(d.path)}
                        title="Browse into this directory"
                      >
                        →
                      </span>
                    </div>
                  ))}
                </div>
              </div>
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
              disabled={isActive}
            />
          </div>
          <button
            className="btn primary"
            onClick={handleStart}
            disabled={isActive}
          >
            Start Scan
          </button>
          {isActive && (
            <button
              className="btn"
              onClick={handleCancel}
              disabled={isCancelling || isPending}
              style={{ color: isCancelling ? 'var(--text-muted)' : 'var(--red)' }}
            >
              {isCancelling ? 'Stopping…' : 'Stop'}
            </button>
          )}
        </div>
      </div>

      {/* Status */}
      {(activeJob || lastJob) && (
        <div className="scan-grid">
          <div className="card">
            <div className="card-title">Status</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span className={`pill ${isRunning ? 'green' : activeJob?.error ? 'red' : 'grey'}`} />
              <span style={{ fontWeight: 600 }}>
                {isPending ? 'Queued'
                  : isCancelling ? 'Stopping'
                  : isRunning ? 'Running'
                  : lastJob?.status === 'completed' ? 'Completed'
                  : lastJob?.status === 'cancelled' ? 'Cancelled'
                  : lastJob?.status === 'failed' ? 'Error'
                  : 'Idle'}
              </span>
            </div>
            {activeJob?.phase === 'discovering' && isRunning && (
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
                <span className="spinner" style={{ marginRight: 6 }} />
                Discovering files… {activeJob.total > 0 && `${activeJob.total.toLocaleString()} found so far`}
              </div>
            )}
            {activeJob?.phase === 'scanning' && isRunning && (
              <>
                <div className="progress-track">
                  <div className="progress-fill" style={{ width: `${pct}%` }} />
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                  {pct}% · {activeJob.progress.toLocaleString()} of {activeJob.total.toLocaleString()} files
                </div>
                <div className="scan-file">{activeJob.current_file}</div>
              </>
            )}
            {isCancelling && (
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 4 }}>
                <span className="spinner" style={{ marginRight: 6 }} />
                Stopping… finishing in-progress checksums
              </div>
            )}
            {lastJob?.error && !activeJob && (
              <div style={{ fontSize: 13, color: 'var(--red)', marginTop: 4 }}>{lastJob.error}</div>
            )}
          </div>

          <div className="card">
            <div className="card-title">Last run</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Files processed</div>
                <div style={{ fontWeight: 700, fontSize: 20 }}>{(displayJob?.progress || 0).toLocaleString()}</div>
              </div>
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Errors</div>
                <div style={{ fontWeight: 700, fontSize: 20, color: (displayJob?.errors || 0) > 0 ? 'var(--red)' : undefined }}>
                  {displayJob?.errors || 0}
                </div>
              </div>
              {displayJob?.started_at && (
                <div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>Started</div>
                  <div style={{ fontSize: 13 }}>{new Date(displayJob.started_at).toLocaleTimeString()}</div>
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
