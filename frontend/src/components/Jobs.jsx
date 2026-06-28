import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

const TYPE_COLORS = {
  ingest:    { bg: '#1e3a5f', color: '#60a5fa' },
  promote:   { bg: '#14532d', color: '#4ade80' },
  scan:      { bg: '#3b1f5e', color: '#c084fc' },
  mediainfo: { bg: '#451a03', color: '#fb923c' },
  workflow:  { bg: '#134e4a', color: '#2dd4bf' },
}

const STATUS_COLORS = {
  running:    'var(--green)',
  pending:    'var(--text-muted)',
  completed:  'var(--green)',
  failed:     'var(--red)',
  cancelled:  'var(--text-muted)',
  cancelling: '#f59e0b',
}

function TypeBadge({ type }) {
  const c = TYPE_COLORS[type] || { bg: '#1e293b', color: 'var(--text-muted)' }
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 4,
      fontSize: 11,
      fontWeight: 700,
      letterSpacing: '0.05em',
      textTransform: 'uppercase',
      background: c.bg,
      color: c.color,
    }}>
      {type}
    </span>
  )
}

function StatusDot({ status }) {
  const active = status === 'running' || status === 'cancelling'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      {active
        ? <span className="spinner" style={{ width: 10, height: 10 }} />
        : <span style={{
            display: 'inline-block',
            width: 8, height: 8,
            borderRadius: '50%',
            background: STATUS_COLORS[status] || 'var(--text-muted)',
          }} />
      }
      <span style={{ color: STATUS_COLORS[status] || 'var(--text-muted)', fontSize: 13 }}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    </span>
  )
}

function jobDescription(job) {
  try {
    const p = typeof job.params === 'string' ? JSON.parse(job.params) : (job.params || {})
    if (job.type === 'ingest')    return p.file_path   ? basename(p.file_path)   : '—'
    if (job.type === 'promote')   return p.staged_path ? basename(p.staged_path) : '—'
    if (job.type === 'scan')      return p.scan_path   || p.path || '—'
    if (job.type === 'mediainfo') {
      if (job._filenames?.length) return job._filenames.join(', ')
      const fids = p.file_ids || (p.file_id != null ? [p.file_id] : [])
      return fids.length ? fids.map(id => `file #${id}`).join(', ') : '—'
    }
    if (job.type === 'workflow')  return p.workflow     || '—'
  } catch (_) {}
  return '—'
}

function jobFullPath(job) {
  try {
    const p = typeof job.params === 'string' ? JSON.parse(job.params) : (job.params || {})
    if (job.type === 'ingest')  return p.file_path   || null
    if (job.type === 'promote') return p.staged_path || null
    if (job.type === 'scan')    return p.scan_path   || null
  } catch (_) {}
  return null
}

function basename(path) {
  return path.split('/').filter(Boolean).pop() || path
}

function duration(job) {
  if (!job.started_at || !job.completed_at) return null
  const secs = Math.round(
    (new Date(job.completed_at) - new Date(job.started_at)) / 1000
  )
  if (secs < 60)   return `${secs}s`
  if (secs < 3600) return `${Math.floor(secs/60)}m ${secs%60}s`
  return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`
}

function relTime(ts) {
  if (!ts) return '—'
  const secs = Math.round((Date.now() - new Date(ts)) / 1000)
  if (secs < 60)   return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs/60)}m ago`
  if (secs < 86400) return `${Math.floor(secs/3600)}h ago`
  return new Date(ts).toLocaleDateString()
}

function ActiveJobCard({ job, onCancel }) {
  const pct = job.total ? Math.round(job.progress / job.total * 100) : 0
  const isScanning = job.type === 'scan'
  const isCancelling = job.status === 'cancelling'

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <TypeBadge type={job.type} />
        <StatusDot status={job.status} />
        <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 'auto' }}>
          #{job.id}
        </span>
        {!isCancelling && (
          <button
            className="btn sm"
            style={{ color: 'var(--red)', fontSize: 12 }}
            onClick={() => onCancel(job.id)}
          >
            Cancel
          </button>
        )}
      </div>

      <div style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, wordBreak: 'break-all' }}>
        {jobFullPath(job) || job.current_file || jobDescription(job)}
      </div>

      {isScanning && job.phase === 'scanning' && job.total > 0 && (
        <>
          <div className="progress-track" style={{ marginBottom: 4 }}>
            <div className="progress-fill" style={{ width: `${pct}%` }} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            {pct}% · {job.progress.toLocaleString()} / {job.total.toLocaleString()} files
          </div>
        </>
      )}
      {isScanning && job.phase === 'discovering' && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
          Discovering files… {job.total > 0 && `${job.total.toLocaleString()} found`}
        </div>
      )}
      {!isScanning && job.current_file && !jobFullPath(job) && (
        <div style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'monospace', wordBreak: 'break-all' }}>
          {job.current_file}
        </div>
      )}
    </div>
  )
}

function SortTh({ col, label, sortCol, sortDir, onSort, style = {} }) {
  const active = sortCol === col
  return (
    <th
      onClick={() => onSort(col)}
      style={{
        padding: '8px 12px', textAlign: 'left', fontWeight: 600,
        cursor: 'pointer', userSelect: 'none',
        color: active ? 'var(--text)' : 'var(--text-muted)',
        ...style,
      }}
    >
      {label}{active ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
    </th>
  )
}

function durSecs(job) {
  if (!job.started_at || !job.completed_at) return -1
  return (new Date(job.completed_at) - new Date(job.started_at)) / 1000
}

export default function Jobs() {
  const [activeJobs, setActiveJobs]   = useState([])
  const [history, setHistory]         = useState([])
  const [filter, setFilter]           = useState('all')  // all | ingest | promote | scan
  const [sortCol, setSortCol]         = useState('id')
  const [sortDir, setSortDir]         = useState('desc')
  const [err, setErr]                 = useState(null)
  const [cancellingIds, setCancellingIds] = useState(new Set())

  const handleSort = (col) => {
    if (col === sortCol) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('desc') }
  }

  const loadHistory = useCallback(async () => {
    try {
      const data = await api.recentJobs(100)
      setHistory(data?.jobs || [])
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  // Poll active jobs every 2 seconds
  useEffect(() => {
    let alive = true
    async function poll() {
      try {
        const job = await api.activeJob()
        if (!alive) return
        // activeJob returns a single job or null
        if (job) {
          setActiveJobs([job])
        } else {
          setActiveJobs([])
          // Refresh history when a job finishes
          loadHistory()
        }
        setErr(null)
      } catch (e) {
        if (alive) setErr(e.message)
      }
    }
    poll()
    const t = setInterval(poll, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [loadHistory])

  // Load history on mount
  useEffect(() => { loadHistory() }, [loadHistory])

  const handleCancel = async (jobId) => {
    setCancellingIds(s => new Set(s).add(jobId))
    try {
      await api.cancelJob(jobId)
    } catch (e) {
      setErr(e.message)
    } finally {
      setCancellingIds(s => { const n = new Set(s); n.delete(jobId); return n })
    }
  }

  const FILTERS = ['all', 'ingest', 'promote', 'scan', 'mediainfo']

  const activeIds = new Set(activeJobs.map(j => j.id))
  const filteredHistory = history
    .filter(j => {
      if (activeIds.has(j.id)) return false
      if (filter === 'all') return true
      return j.type === filter
    })
    .sort((a, b) => {
      let av, bv
      if (sortCol === 'id')       { av = a.id;                              bv = b.id }
      else if (sortCol === 'type')   { av = a.type;                            bv = b.type }
      else if (sortCol === 'status') { av = a.status;                          bv = b.status }
      else if (sortCol === 'desc')   { av = jobDescription(a);                 bv = jobDescription(b) }
      else if (sortCol === 'when')   { av = new Date(a.started_at || a.created_at); bv = new Date(b.started_at || b.created_at) }
      else if (sortCol === 'dur')    { av = durSecs(a);                        bv = durSecs(b) }
      else                           { av = a.id;                              bv = b.id }
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1  : -1
      return 0
    })

  const statusCounts = history.reduce((acc, j) => {
    if (activeIds.has(j.id)) return acc
    acc[j.status] = (acc[j.status] || 0) + 1
    return acc
  }, {})

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Jobs</div>
          <div className="section-sub">Monitor and manage all pipeline jobs</div>
        </div>
        <button className="btn sm" onClick={loadHistory}>Refresh</button>
      </div>

      {err && <div className="error-box">{err}</div>}

      {/* Summary strip */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        {[
          { label: 'Completed', key: 'completed', color: 'var(--green)' },
          { label: 'Failed',    key: 'failed',    color: 'var(--red)' },
          { label: 'Cancelled', key: 'cancelled',  color: 'var(--text-muted)' },
        ].map(({ label, key, color }) => (
          <div key={key} className="card" style={{ padding: '10px 20px', minWidth: 100 }}>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
            <div style={{ fontWeight: 700, fontSize: 22, color }}>{statusCounts[key] || 0}</div>
          </div>
        ))}
      </div>

      {/* Active jobs */}
      {activeJobs.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Active
          </div>
          {activeJobs.map(j => (
            <ActiveJobCard key={j.id} job={j} onCancel={handleCancel} />
          ))}
        </div>
      )}

      {activeJobs.length === 0 && (
        <div className="card" style={{ marginBottom: 16, color: 'var(--text-muted)', fontSize: 13, padding: '12px 16px' }}>
          No active jobs
        </div>
      )}

      {/* Filter pills */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 12, flexWrap: 'wrap' }}>
        {FILTERS.map(f => (
          <button
            key={f}
            className={`btn sm${filter === f ? ' primary' : ''}`}
            onClick={() => setFilter(f)}
            style={{ textTransform: 'capitalize' }}
          >
            {f}
          </button>
        ))}
      </div>

      {/* History table */}
      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
              <SortTh col="id"     label="#"          sortCol={sortCol} sortDir={sortDir} onSort={handleSort} style={{ width: 40 }} />
              <SortTh col="type"   label="Type"       sortCol={sortCol} sortDir={sortDir} onSort={handleSort} style={{ width: 90 }} />
              <SortTh col="status" label="Status"     sortCol={sortCol} sortDir={sortDir} onSort={handleSort} style={{ width: 110 }} />
              <SortTh col="desc"   label="File / Path" sortCol={sortCol} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="when"   label="When"       sortCol={sortCol} sortDir={sortDir} onSort={handleSort} style={{ width: 90 }} />
              <SortTh col="dur"    label="Duration"   sortCol={sortCol} sortDir={sortDir} onSort={handleSort} style={{ width: 70 }} />
            </tr>
          </thead>
          <tbody>
            {filteredHistory.length === 0 && (
              <tr>
                <td colSpan={6} style={{ padding: '24px 12px', textAlign: 'center', color: 'var(--text-muted)' }}>
                  No jobs found
                </td>
              </tr>
            )}
            {filteredHistory.map(job => (
              <tr
                key={job.id}
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                <td style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: 12 }}>
                  {job.id}
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <TypeBadge type={job.type} />
                </td>
                <td style={{ padding: '8px 12px' }}>
                  <StatusDot status={job.status} />
                </td>
                <td style={{ padding: '8px 12px', fontFamily: 'monospace', fontSize: 12, color: 'var(--text-muted)', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  <span title={jobFullPath(job) || ''}>
                    {jobDescription(job)}
                  </span>
                  {job.error && (
                    <div style={{ color: 'var(--red)', fontFamily: 'sans-serif', fontSize: 11, marginTop: 2, whiteSpace: 'normal' }}>
                      {job.error}
                    </div>
                  )}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: 12, whiteSpace: 'nowrap' }}>
                  {relTime(job.started_at || job.created_at)}
                </td>
                <td style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: 12, whiteSpace: 'nowrap' }}>
                  {duration(job) || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
