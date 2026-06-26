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

export default function Files() {
  const [data, setData]       = useState(null)
  const [page, setPage]       = useState(0)
  const [search, setSearch]   = useState('')
  const [filter, setFilter]   = useState('all')
  const [err, setErr]         = useState(null)
  const [busy, setBusy]       = useState(false)
  const [preview, setPreview] = useState(null)

  const load = useCallback(async (p) => {
    setBusy(true)
    try {
      const params = {
        limit: PAGE,
        offset: p * PAGE,
        ...(search && { search }),
        ...(filter === 'duplicates' && { duplicates_only: true }),
        ...(filter === 'marked'     && { marked_only: true }),
      }
      const d = await api.files(params)
      setData(d)
      setErr(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }, [search, filter])

  // Reset to page 0 when search/filter changes
  useEffect(() => {
    setPage(0)
    load(0)
  }, [search, filter])

  // Load when page changes (but not on filter/search — handled above)
  useEffect(() => {
    if (page !== 0) load(page)
  }, [page])

  const handleToggleMark = async (id, mark) => {
    await api.markFile(id, mark)
    load(page)
  }

  const pages = data ? Math.ceil(data.total / PAGE) : 0

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">File Inventory</div>
          <div className="section-sub">
            {data ? `${data.total.toLocaleString()} files` : ''}
          </div>
        </div>
        {busy && <div className="spinner" />}
      </div>

      {err && <div className="error-box">{err}</div>}

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
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
              className={`btn sm${filter === f ? ' primary' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'All' : f === 'duplicates' ? 'Duplicates' : 'Marked'}
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
                <th>Filename</th>
                <th>Path</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th>Scanned</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.files.map(f => (
                <tr key={f.id} className={f.marked_for_deletion ? 'row-marked' : ''}>
                  <td className="cell-name">{f.filename}</td>
                  <td className="cell-path">{f.filepath}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmt(f.filesize)}</td>
                  <td style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 12 }}>
                    {new Date(f.scan_date).toLocaleDateString()}
                  </td>
                  <td>
                    {f.marked_for_deletion
                      ? <span className="badge delete">marked</span>
                      : <span className="badge" style={{ color: 'var(--text-muted)', background: 'var(--surface2)' }}>ok</span>
                    }
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button
                      className="btn sm"
                      style={{ marginRight: 6 }}
                      onClick={() => setPreview(f)}
                    >
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
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
            {page + 1} / {pages}
          </span>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>›</button>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)}>»</button>
        </div>
      )}
      <FilePreview file={preview} onClose={() => setPreview(null)} />
    </>
  )
}
