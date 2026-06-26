import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import FilePreview from './FilePreview'

const PAGE = 25

function fmt(bytes) {
  if (!bytes) return '0 B'
  if (bytes >= 1e9)  return (bytes / 1e9).toFixed(2)  + ' GB'
  if (bytes >= 1e6)  return (bytes / 1e6).toFixed(2)  + ' MB'
  if (bytes >= 1e3)  return (bytes / 1e3).toFixed(1)  + ' KB'
  return bytes + ' B'
}

function DupGroup({ group, onToggleMark, onAutoMark, onPreview }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="dup-group">
      <div className="dup-group-header" onClick={() => setOpen(o => !o)}>
        <span style={{ fontSize: 12, color: 'var(--text-muted)', marginRight: 4 }}>
          {open ? '▾' : '▸'}
        </span>
        <span className="dup-hash">{group.sha256.slice(0, 16)}…</span>
        <span className="dup-count">{group.count} copies</span>
        <span className="dup-size">{fmt(group.total_size)}</span>
        <button
          className="btn sm"
          onClick={e => { e.stopPropagation(); onAutoMark(group.sha256) }}
          title="Auto-mark all but best copy for deletion"
        >
          Auto-mark
        </button>
      </div>

      {open && (
        <div className="dup-files">
          {group.files.map(file => (
            <div
              key={file.id}
              className={`dup-file-row${file.marked_for_deletion ? ' marked' : ''}`}
            >
              <div className="dup-file-info">
                <div className="dup-file-name">{file.filename}</div>
                <div className="dup-file-path">{file.filepath}</div>
              </div>
              <div className="dup-file-size">{fmt(file.filesize)}</div>
              {file.suggested_keep && <span className="badge keep">keep</span>}
              {file.marked_for_deletion
                ? <span className="badge delete">delete</span>
                : null
              }
              <button className="btn sm" onClick={() => onPreview(file)}>Preview</button>
              <button
                className={`btn sm${file.marked_for_deletion ? '' : ' danger'}`}
                onClick={() => onToggleMark(file.id, !file.marked_for_deletion)}
              >
                {file.marked_for_deletion ? 'Unmark' : 'Mark'}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Duplicates() {
  const [data, setData]       = useState(null)
  const [page, setPage]       = useState(0)
  const [err, setErr]         = useState(null)
  const [busy, setBusy]       = useState(false)
  const [preview, setPreview] = useState(null)

  const load = useCallback(async (p = page) => {
    setBusy(true)
    try {
      const d = await api.duplicates(PAGE, p * PAGE)
      setData(d)
      setErr(null)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }, [page])

  useEffect(() => { load(page) }, [page])

  const handleToggleMark = async (id, mark) => {
    await api.markFile(id, mark)
    load(page)
  }

  const handleAutoMark = async (sha256) => {
    await api.autoMark(sha256)
    load(page)
  }

  const pages = data ? Math.ceil(data.total / PAGE) : 0

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Duplicate Groups</div>
          <div className="section-sub">
            {data ? `${data.total.toLocaleString()} groups · Page ${page + 1} of ${Math.max(1, pages)}` : ''}
          </div>
        </div>
        {busy && <div className="spinner" />}
      </div>

      {err && <div className="error-box">{err}</div>}

      {data?.groups.map(g => (
        <DupGroup
          key={g.sha256}
          group={g}
          onToggleMark={handleToggleMark}
          onAutoMark={handleAutoMark}
          onPreview={setPreview}
        />
      ))}

      {data?.groups.length === 0 && (
        <div className="card" style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 40 }}>
          No duplicates found. Run a scan first.
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
