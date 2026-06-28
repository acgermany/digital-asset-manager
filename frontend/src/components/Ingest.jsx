import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import FilePreview from './FilePreview'

const PAGE = 50

const CATEGORIES = [
  'Photos',
  'HomeVideos',
  'Audio/Recordings',
  'Audio/Music',
  'Art/Visual',
  'Art/Video',
  'Documents',
]

const CATEGORY_COLORS = {
  'Photos':           { bg: '#1a3a2a', color: '#4ade80' },
  'HomeVideos':       { bg: '#1a2a3a', color: '#60a5fa' },
  'Audio/Recordings': { bg: '#2a1a3a', color: '#c084fc' },
  'Audio/Music':      { bg: '#3a1a3a', color: '#f0abfc' },
  'Art/Visual':       { bg: '#3a2a1a', color: '#fb923c' },
  'Art/Video':        { bg: '#3a1a1a', color: '#f87171' },
  'Documents':        { bg: '#2a2a1a', color: '#fbbf24' },
}

function fmt(bytes) {
  if (!bytes) return '0 B'
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + ' GB'
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(2) + ' MB'
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + ' KB'
  return bytes + ' B'
}

function mediaType(filename) {
  const ext = (filename?.split('.').pop() || '').toLowerCase()
  if (['jpg','jpeg','png','heic','heif','webp','gif','tiff','tif','avif'].includes(ext)) return 'image'
  if (['mp4','mov','avi','mkv','m4v','ts','mxf','mpg'].includes(ext)) return 'video'
  if (['mp3','m4a','wav','flac','aac','ogg','opus','aiff'].includes(ext)) return 'audio'
  if (ext === 'pdf') return 'pdf'
  return 'other'
}

function TypeIcon({ filename }) {
  const t = mediaType(filename)
  const icons = { image: '🖼', video: '🎬', audio: '🎵', pdf: '📄', other: '📁' }
  return <span style={{ fontSize: 20 }}>{icons[t] || '📁'}</span>
}

function CategoryBadge({ category, fileId, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [busy, setBusy]       = useState(false)
  const style = CATEGORY_COLORS[category] || { bg: 'var(--surface2)', color: 'var(--text-muted)' }

  const handleChange = async (e) => {
    const newCat = e.target.value
    if (newCat === category) { setEditing(false); return }
    setBusy(true)
    try {
      await api.setStagedCategory(fileId, newCat)
      onChanged(newCat)
    } catch (err) {
      console.error(err)
    } finally {
      setBusy(false)
      setEditing(false)
    }
  }

  if (editing) {
    return (
      <select
        className="search"
        style={{ fontSize: 12, padding: '2px 6px' }}
        value={category}
        onChange={handleChange}
        onBlur={() => setEditing(false)}
        autoFocus
        disabled={busy}
      >
        {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
    )
  }

  return (
    <span
      className="badge"
      style={{
        background: style.bg, color: style.color,
        cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap',
        fontSize: 11, padding: '2px 8px',
      }}
      title="Click to change category"
      onClick={() => setEditing(true)}
    >
      {category || '—'}
    </span>
  )
}

function PathEditor({ fileId, value, onChanged }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState(value)
  const [busy, setBusy]       = useState(false)

  const save = async () => {
    if (draft === value) { setEditing(false); return }
    setBusy(true)
    try {
      await api.setStagedPath(fileId, draft)
      onChanged(draft)
    } catch (err) {
      console.error(err)
    } finally {
      setBusy(false)
      setEditing(false)
    }
  }

  if (!editing) {
    return (
      <span
        className="cell-path"
        style={{ cursor: 'pointer', textDecoration: 'underline dotted' }}
        title="Click to edit archive path"
        onClick={() => { setDraft(value); setEditing(true) }}
      >
        {value || '—'}
      </span>
    )
  }

  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
      <input
        className="search"
        style={{ fontSize: 11, padding: '2px 6px', flex: 1, minWidth: 0 }}
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }}
        autoFocus
        disabled={busy}
      />
      <button className="btn sm primary" onClick={save} disabled={busy}>✓</button>
      <button className="btn sm" onClick={() => setEditing(false)}>✕</button>
    </div>
  )
}

function StatsBar({ stats, onRefresh }) {
  return (
    <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
      <StatCard label="Staged"    value={stats?.staged    ?? '—'} color="var(--accent-lt)" />
      <StatCard label="Archived"  value={stats?.archived  ?? '—'} color="#4ade80" />
      <StatCard label="Duplicate" value={stats?.duplicate ?? '—'} color="#fbbf24" />
      <StatCard label="Rejected"  value={stats?.rejected  ?? '—'} color="var(--red)" />
      <button className="btn sm" style={{ marginLeft: 'auto' }} onClick={onRefresh}>Refresh</button>
    </div>
  )
}

function StatCard({ label, value, color }) {
  return (
    <div className="card" style={{ padding: '10px 18px', minWidth: 90, textAlign: 'center' }}>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{label}</div>
    </div>
  )
}

function IngestTriggerModal({ onClose, onDone }) {
  const [filePath, setFilePath]       = useState('')
  const [source, setSource]           = useState('manual')
  const [initials, setInitials]       = useState('')
  const [busy, setBusy]               = useState(false)
  const [err, setErr]                 = useState(null)
  const [done, setDone]               = useState(false)

  const handleSubmit = async () => {
    if (!filePath.trim()) return
    setBusy(true)
    setErr(null)
    try {
      await api.triggerIngest(filePath.trim(), source, initials.trim())
      setDone(true)
      onDone()
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 520 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">Trigger Ingest</span>
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body modal-body-light" style={{ padding: 20 }}>
          {done ? (
            <div style={{ textAlign: 'center', padding: 16 }}>
              <div style={{ marginBottom: 8 }}>Ingest job queued.</div>
              <button className="btn sm primary" onClick={onClose}>Close</button>
            </div>
          ) : (
            <>
              <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                File path (absolute, on the NAS)
              </label>
              <input
                className="search"
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="/nas/ingest/myfile.jpg"
                value={filePath}
                onChange={e => setFilePath(e.target.value)}
              />
              <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Source
                  </label>
                  <select className="search" style={{ width: '100%' }} value={source} onChange={e => setSource(e.target.value)}>
                    <option value="manual">Manual</option>
                    <option value="hot_folder">Hot Folder</option>
                    <option value="phone_dump">Phone Dump</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>
                    Initials (optional)
                  </label>
                  <input
                    className="search"
                    style={{ width: '100%' }}
                    placeholder="ag"
                    value={initials}
                    onChange={e => setInitials(e.target.value)}
                  />
                </div>
              </div>
              {err && <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12 }}>{err}</div>}
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn sm primary" onClick={handleSubmit} disabled={busy || !filePath.trim()}>
                  {busy ? 'Queuing…' : 'Queue Job'}
                </button>
                <button className="btn sm" onClick={onClose}>Cancel</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function WatcherSources() {
  const [sources, setSources] = useState([])
  const [toggling, setToggling] = useState(new Set())

  useEffect(() => {
    api.watcherSources()
      .then(d => setSources(d.sources || []))
      .catch(() => {})
  }, [])

  const handleToggle = async (id, enabled) => {
    setToggling(s => new Set(s).add(id))
    try {
      await api.toggleWatcherSource(id, enabled)
      setSources(s => s.map(src => src.id === id ? { ...src, enabled: enabled ? 1 : 0 } : src))
    } catch (e) {
      console.error(e)
    } finally {
      setToggling(s => { const n = new Set(s); n.delete(id); return n })
    }
  }

  if (!sources.length) return null

  const typeLabel = { hot_folder: 'Hot Folder', phone_dump: 'Phone Dump' }

  return (
    <div className="card" style={{ marginBottom: 20, padding: '12px 16px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Watch Sources
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {sources.map(src => (
          <div key={src.id} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <label style={{ position: 'relative', display: 'inline-block', width: 36, height: 20, flexShrink: 0 }}>
              <input
                type="checkbox"
                checked={!!src.enabled}
                disabled={toggling.has(src.id)}
                onChange={e => handleToggle(src.id, e.target.checked)}
                style={{ opacity: 0, width: 0, height: 0 }}
              />
              <span style={{
                position: 'absolute', cursor: 'pointer', inset: 0,
                background: src.enabled ? 'var(--accent)' : 'var(--surface2)',
                borderRadius: 20, transition: '0.2s',
              }}>
                <span style={{
                  position: 'absolute', content: '', height: 14, width: 14,
                  left: src.enabled ? 18 : 3, bottom: 3,
                  background: 'white', borderRadius: '50%', transition: '0.2s',
                  display: 'block',
                }} />
              </span>
            </label>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>{src.name}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {typeLabel[src.source_type] || src.source_type} · {src.path}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Ingest() {
  const [data, setData]       = useState(null)
  const [stats, setStats]     = useState(null)
  const [page, setPage]       = useState(0)
  const [busy, setBusy]       = useState(false)
  const [err, setErr]         = useState(null)
  const [toast, setToast]     = useState(null)
  const [preview, setPreview] = useState(null)
  const [showTrigger, setShowTrigger] = useState(false)
  const [approving, setApproving]     = useState(new Set())
  const [rejecting, setRejecting]     = useState(new Set())

  const showToast = (msg, isErr = false) => {
    setToast({ msg, isErr })
    setTimeout(() => setToast(null), 3000)
  }

  const loadStats = useCallback(async () => {
    try {
      const s = await api.ingestStats()
      setStats(s)
    } catch (_) {}
  }, [])

  const loadStaged = useCallback(async (p) => {
    setBusy(true)
    try {
      const d = await api.staged({ limit: PAGE, offset: p * PAGE })
      setData(d)
      setErr(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }, [])

  const refresh = useCallback(() => {
    loadStats()
    loadStaged(page)
  }, [loadStats, loadStaged, page])

  useEffect(() => { loadStats(); loadStaged(0) }, [])
  useEffect(() => { if (page !== 0) loadStaged(page) }, [page])

  const updateFile = (id, patch) => {
    setData(d => d ? {
      ...d,
      files: d.files.map(f => f.id === id ? { ...f, ...patch } : f),
    } : d)
  }

  const handleApprove = async (id) => {
    setApproving(s => new Set(s).add(id))
    try {
      const result = await api.approveStaged(id)
      setData(d => d ? { ...d, files: d.files.filter(f => f.id !== id), total: d.total - 1 } : d)
      loadStats()
      showToast(`Queued for archive (job #${result.job_id})`)
    } catch (e) {
      const msg = e.message.includes('404') ? 'File already archived or not found' : e.message
      showToast(msg, true)
    } finally {
      setApproving(s => { const n = new Set(s); n.delete(id); return n })
    }
  }

  const handleReject = async (id) => {
    setRejecting(s => new Set(s).add(id))
    try {
      await api.rejectStaged(id)
      setData(d => d ? { ...d, files: d.files.filter(f => f.id !== id), total: d.total - 1 } : d)
      loadStats()
      showToast('Moved to rejected')
    } catch (e) {
      showToast(e.message, true)
    } finally {
      setRejecting(s => { const n = new Set(s); n.delete(id); return n })
    }
  }

  const handleApproveAll = async () => {
    setBusy(true)
    try {
      const result = await api.approveAllStaged()
      loadStaged(0)
      loadStats()
      showToast(`Queued ${result.queued} file${result.queued !== 1 ? 's' : ''} for archive`)
    } catch (e) {
      const msg = e.message.includes('409') ? 'Archive path conflicts — review individually' : e.message
      showToast(msg, true)
    } finally {
      setBusy(false)
    }
  }

  const pages = data ? Math.ceil(data.total / PAGE) : 0

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Ingest / Staging Review</div>
          <div className="section-sub">
            {data ? `${data.total.toLocaleString()} file${data.total !== 1 ? 's' : ''} awaiting review` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {data?.total > 0 && (
            <button className="btn sm primary" onClick={handleApproveAll} disabled={busy}>
              Approve All
            </button>
          )}
          <button className="btn sm" onClick={() => setShowTrigger(true)}>
            + Trigger Ingest
          </button>
          {busy && <div className="spinner" />}
        </div>
      </div>

      <WatcherSources />

      <StatsBar stats={stats} onRefresh={refresh} />

      {err && <div className="error-box">{err}</div>}

      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 9999,
          background: toast.isErr ? 'var(--red)' : 'var(--accent)',
          color: 'white', padding: '10px 18px', borderRadius: 6,
          fontSize: 13, fontWeight: 500, boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
          pointerEvents: 'none',
        }}>
          {toast.msg}
        </div>
      )}

      {data?.files.length === 0 && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
          No files awaiting review. Drop files into <code>ingest/</code> or trigger a manual ingest.
        </div>
      )}

      {data?.files.length > 0 && (
        <div className="files-table-wrap">
          <table className="files-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>Type</th>
                <th>Original → Proposed</th>
                <th>Category</th>
                <th>Archive Path</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.files.map(f => {
                const isImg     = mediaType(f.filename) === 'image'
                const approveBusy = approving.has(f.id)
                const rejectBusy  = rejecting.has(f.id)
                return (
                  <tr key={f.id}>
                    <td style={{ textAlign: 'center' }}>
                      {isImg ? (
                        <img
                          src={`/api/files/${f.id}/content`}
                          alt=""
                          style={{
                            width: 36, height: 36, objectFit: 'cover',
                            borderRadius: 4, cursor: 'pointer',
                          }}
                          onClick={() => setPreview(f)}
                        />
                      ) : (
                        <span
                          style={{ cursor: 'pointer' }}
                          onClick={() => setPreview(f)}
                        >
                          <TypeIcon filename={f.filename} />
                        </span>
                      )}
                    </td>
                    <td>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 2 }}>
                        {f.original_filename || '—'}
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className="cell-name" style={{ fontSize: 13 }}>
                          {f.filename}
                        </span>
                        {f.collision ? (
                          <span
                            title="Filename collision — a suffix was added to make this name unique"
                            style={{
                              fontSize: 10, fontWeight: 700, padding: '1px 5px',
                              borderRadius: 3, background: '#451a03', color: '#fb923c',
                              letterSpacing: '0.05em', whiteSpace: 'nowrap',
                            }}
                          >
                            RENAMED
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <CategoryBadge
                        category={f.category}
                        fileId={f.id}
                        onChanged={cat => updateFile(f.id, { category: cat })}
                      />
                    </td>
                    <td style={{ maxWidth: 320 }}>
                      <PathEditor
                        fileId={f.id}
                        value={f.archive_path}
                        onChanged={path => updateFile(f.id, { archive_path: path })}
                      />
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 12 }}>
                      {fmt(f.filesize)}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button
                        className="btn sm primary"
                        style={{ marginRight: 6 }}
                        onClick={() => handleApprove(f.id)}
                        disabled={approveBusy || rejectBusy}
                      >
                        {approveBusy ? '…' : 'Approve'}
                      </button>
                      <button
                        className="btn sm danger"
                        onClick={() => handleReject(f.id)}
                        disabled={approveBusy || rejectBusy}
                      >
                        {rejectBusy ? '…' : 'Reject'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {pages > 1 && (
        <div className="pagination">
          <button className="btn sm" disabled={page === 0} onClick={() => setPage(0)}>«</button>
          <button className="btn sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹</button>
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{page + 1} / {pages}</span>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>›</button>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)}>»</button>
        </div>
      )}

      <FilePreview file={preview} onClose={() => setPreview(null)} />

      {showTrigger && (
        <IngestTriggerModal
          onClose={() => setShowTrigger(false)}
          onDone={refresh}
        />
      )}
    </>
  )
}
