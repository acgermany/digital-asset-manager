import { useState, useEffect } from 'react'
import { api } from '../api'

function fmt(bytes) {
  if (!bytes) return '0 B'
  if (bytes >= 1e12) return (bytes / 1e12).toFixed(2) + ' TB'
  if (bytes >= 1e9)  return (bytes / 1e9).toFixed(2)  + ' GB'
  if (bytes >= 1e6)  return (bytes / 1e6).toFixed(2)  + ' MB'
  if (bytes >= 1e3)  return (bytes / 1e3).toFixed(1)  + ' KB'
  return bytes + ' B'
}

function StatCard({ title, value, sub, accent }) {
  return (
    <div className="card">
      <div className="card-title">{title}</div>
      <div className="card-value" style={accent ? { color: accent } : {}}>{value}</div>
      {sub && <div className="card-sub">{sub}</div>}
    </div>
  )
}

export default function Dashboard({ onGoScan }) {
  const [stats, setStats] = useState(null)
  const [scan, setScan]   = useState(null)
  const [err, setErr]     = useState(null)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const [s, sc] = await Promise.all([api.stats(), api.activeJob()])
        if (!alive) return
        setStats(s)
        setScan(sc)
      } catch (e) {
        if (alive) setErr(e.message)
      }
    }
    load()
    const t = setInterval(load, 5000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  if (err) return <div className="error-box">API unavailable: {err}</div>
  if (!stats) return <div style={{ color: 'var(--text-muted)' }}>Loading…</div>

  const pct = stats.total_files
    ? Math.round(stats.duplicate_files / stats.total_files * 100)
    : 0

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Overview</div>
          <div className="section-sub">Inventory statistics from the last scan</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className={`pill ${scan ? 'green' : 'grey'}`} />
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {scan ? 'Scan running…' : 'No scan running'}
          </span>
          {!scan && (
            <button className="btn sm primary" onClick={onGoScan}>Scan now</button>
          )}
        </div>
      </div>

      <div className="stat-grid">
        <StatCard
          title="Total Files"
          value={stats.total_files.toLocaleString()}
          sub={fmt(stats.total_size)}
        />
        <StatCard
          title="Unique Files"
          value={stats.unique_files.toLocaleString()}
          sub={`${stats.total_files - stats.unique_files} have duplicates`}
        />
        <StatCard
          title="Duplicate Copies"
          value={stats.duplicate_files.toLocaleString()}
          sub={`${pct}% of inventory`}
          accent="var(--yellow)"
        />
        <StatCard
          title="Wasted Space"
          value={fmt(stats.wasted_space)}
          sub="reclaimable from deduplication"
          accent="var(--red)"
        />
        <StatCard
          title="Marked for Deletion"
          value={stats.marked_for_deletion.toLocaleString()}
          sub={fmt(stats.marked_size)}
          accent={stats.marked_for_deletion > 0 ? 'var(--red)' : undefined}
        />
        <StatCard
          title="Duplicate Size"
          value={fmt(stats.duplicate_size)}
          sub="total size of all duplicate copies"
        />
      </div>

      {scan && (
        <div className="card" style={{ marginTop: 8 }}>
          <div className="card-title">Scan in progress</div>
          <div className="progress-track">
            <div
              className="progress-fill"
              style={{ width: scan.total ? `${Math.round(scan.progress / scan.total * 100)}%` : '0%' }}
            />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)' }}>
            <span>{(scan.progress || 0).toLocaleString()} / {(scan.total || 0).toLocaleString()} files</span>
            <span>{scan.errors || 0} errors</span>
          </div>
          <div className="scan-file">{scan.current_file}</div>
        </div>
      )}
    </>
  )
}
