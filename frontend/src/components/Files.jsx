import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import FilePreview from './FilePreview'

const PAGE = 50

function fmt(bytes) {
  if (!bytes) return '0 B'
  if (bytes >= 1e9)  return (bytes / 1e9).toFixed(2)  + ' GB'
  if (bytes >= 1e6)  return (bytes / 1e6).toFixed(2)  + ' MB'
  if (bytes >= 1e3)  return (bytes / 1e3).toFixed(1)  + ' KB'
  return bytes + ' B'
}

function WorkflowModal({ selectedIds, onClose, onDone }) {
  const [workflows, setWorkflows] = useState([])
  const [chosen,    setChosen]    = useState(null)
  const [running,   setRunning]   = useState(false)
  const [err,       setErr]       = useState(null)
  const [done,      setDone]      = useState(false)

  useEffect(() => {
    api.workflows().then(d => {
      setWorkflows(d.workflows || [])
      if (d.workflows?.length) setChosen(d.workflows[0].name)
    }).catch(e => setErr(e.message))
  }, [])

  const handleRun = async () => {
    if (!chosen) return
    setRunning(true)
    setErr(null)
    try {
      await api.runWorkflow(chosen, selectedIds)
      setDone(true)
      onDone()
    } catch (e) {
      setErr(e.message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 480 }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">Run Workflow</span>
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body modal-body-light" style={{ padding: 20 }}>
          {done ? (
            <div style={{ textAlign: 'center', padding: 24 }}>
              <div style={{ fontSize: 15, marginBottom: 8 }}>Job queued successfully.</div>
              <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 20 }}>
                {selectedIds.length} file{selectedIds.length !== 1 ? 's' : ''} — monitor progress in Scan Control.
              </div>
              <button className="btn sm primary" onClick={onClose}>Close</button>
            </div>
          ) : (
            <>
              <div style={{ marginBottom: 16, fontSize: 13, color: 'var(--text-muted)' }}>
                {selectedIds.length} file{selectedIds.length !== 1 ? 's' : ''} selected
              </div>
              {workflows.length === 0 ? (
                <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>
                  No workflow scripts found in <code>/workflows</code>. Add executable scripts there to get started.
                </div>
              ) : (
                <>
                  <label style={{ display: 'block', fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
                    Workflow
                  </label>
                  <select
                    className="search"
                    style={{ width: '100%', marginBottom: 16 }}
                    value={chosen || ''}
                    onChange={e => setChosen(e.target.value)}
                  >
                    {workflows.map(w => (
                      <option key={w.name} value={w.name}>{w.name}</option>
                    ))}
                  </select>
                  {err && <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12 }}>{err}</div>}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn sm primary" onClick={handleRun} disabled={running || !chosen}>
                      {running ? 'Queuing…' : 'Run'}
                    </button>
                    <button className="btn sm" onClick={onClose}>Cancel</button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Files() {
  const [data, setData]             = useState(null)
  const [page, setPage]             = useState(0)
  const [search, setSearch]         = useState('')
  const [viewFilter, setViewFilter] = useState('all')
  const [typeFilter, setTypeFilter] = useState('')
  const [sortCol, setSortCol]       = useState('scan_date')
  const [sortDir, setSortDir]       = useState('desc')
  const [err, setErr]               = useState(null)
  const [busy, setBusy]             = useState(false)
  const [preview, setPreview]       = useState(null)
  const [selected, setSelected]     = useState(new Set())
  const [showWorkflow, setShowWorkflow] = useState(false)

  const load = useCallback(async (p) => {
    setBusy(true)
    try {
      const params = {
        limit: PAGE,
        offset: p * PAGE,
        ...(search && { search }),
        ...(viewFilter === 'duplicates' && { duplicates_only: true }),
        ...(viewFilter === 'marked'     && { marked_only: true }),
        ...(typeFilter && { media_type: typeFilter }),
        sort_by: sortCol,
        sort_dir: sortDir,
      }
      const d = await api.files(params)
      setData(d)
      setSelected(new Set())
      setErr(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }, [search, viewFilter, typeFilter, sortCol, sortDir])

  const handleSort = (col) => {
    if (col === sortCol) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortCol(col); setSortDir('desc') }
    setPage(0)
  }

  useEffect(() => { setPage(0); load(0) }, [search, viewFilter, typeFilter, sortCol, sortDir])
  useEffect(() => { if (page !== 0) load(page) }, [page])

  const handleToggleMark = async (id, mark) => {
    await api.markFile(id, mark)
    load(page)
  }

  const toggleSelect = (id) => {
    setSelected(s => {
      const next = new Set(s)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    if (!data) return
    const ids = data.files.map(f => f.id)
    setSelected(s => s.size === ids.length ? new Set() : new Set(ids))
  }

  const pages = data ? Math.ceil(data.total / PAGE) : 0
  const allChecked = data?.files.length > 0 && selected.size === data.files.length

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">File Inventory</div>
          <div className="section-sub">{data ? `${data.total.toLocaleString()} files` : ''}</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {selected.size > 0 && (
            <button className="btn sm primary" onClick={() => setShowWorkflow(true)}>
              Run Workflow ({selected.size})
            </button>
          )}
          {busy && <div className="spinner" />}
        </div>
      </div>

      {err && <div className="error-box">{err}</div>}

      <div style={{ display: 'flex', gap: 12, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          className="search"
          style={{ width: 320 }}
          placeholder="Search by path…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <div className="filter-bar">
          {['all', 'duplicates', 'marked'].map(f => (
            <button
              key={f}
              className={`btn sm${viewFilter === f ? ' primary' : ''}`}
              onClick={() => setViewFilter(f)}
            >
              {f === 'all' ? 'All' : f === 'duplicates' ? 'Duplicates' : 'Marked'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        <div className="filter-bar">
          {[['', 'All types'], ['video', 'Video'], ['audio', 'Audio'], ['image', 'Image'], ['pdf', 'PDF'], ['text', 'Text'], ['other', 'Other']].map(([val, label]) => (
            <button
              key={val}
              className={`btn sm${typeFilter === val ? ' primary' : ''}`}
              onClick={() => setTypeFilter(val)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {data?.files.length === 0 && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
          No files found. Run a scan first.
        </div>
      )}

      {data?.files.length > 0 && (
        <div className="files-table-wrap">
          <table className="files-table">
            <thead>
              <tr>
                <th style={{ width: 32 }}>
                  <input type="checkbox" checked={allChecked} onChange={toggleAll} />
                </th>
                {[
                  { col: 'filename',  label: 'Filename' },
                  { col: 'filepath',  label: 'Path' },
                  { col: 'filesize',  label: 'Size',    style: { textAlign: 'right' } },
                  { col: 'scan_date', label: 'Scanned' },
                  { col: 'sha256',    label: 'Hash' },
                  { col: 'marked_for_deletion', label: 'Status' },
                ].map(({ col, label, style = {} }) => (
                  <th
                    key={col}
                    onClick={() => handleSort(col)}
                    style={{ cursor: 'pointer', userSelect: 'none', ...style }}
                  >
                    {label}{sortCol === col ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.files.map(f => (
                <tr key={f.id} className={f.marked_for_deletion ? 'row-marked' : ''}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(f.id)}
                      onChange={() => toggleSelect(f.id)}
                    />
                  </td>
                  <td className="cell-name">{f.filename}</td>
                  <td className="cell-path">{f.filepath}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmt(f.filesize)}</td>
                  <td style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 12 }}>
                    {new Date(f.scan_date).toLocaleDateString()}
                  </td>
                  <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                    {f.sha256 ? `…${f.sha256.slice(-8)}` : '—'}
                  </td>
                  <td>
                    {f.marked_for_deletion
                      ? <span className="badge delete">marked</span>
                      : <span className="badge" style={{ color: 'var(--text-muted)', background: 'var(--surface2)' }}>ok</span>
                    }
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn sm" style={{ marginRight: 6 }} onClick={() => setPreview(f)}>
                      Preview
                    </button>
                    <button
                      className={`btn sm${f.marked_for_deletion ? '' : ' danger'}`}
                      onClick={() => handleToggleMark(f.id, !f.marked_for_deletion)}
                    >
                      {f.marked_for_deletion ? 'Unmark' : 'Mark'}
                    </button>
                  </td>
                </tr>
              ))}
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

      {showWorkflow && (
        <WorkflowModal
          selectedIds={[...selected]}
          onClose={() => setShowWorkflow(false)}
          onDone={() => setSelected(new Set())}
        />
      )}
    </>
  )
}
