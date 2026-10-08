import { useState, useEffect, useCallback, useRef } from 'react'
import { api, fmtBytes } from '../api'
import { useAIStatus } from '../hooks'
import SemanticToggle from './SemanticToggle'
import Viewer from './Viewer'
import SimilarityMap from './SimilarityMap'

const PAGE = 60
const KINDS = [['', 'All'], ['image', 'Images'], ['video', 'Videos'], ['audio', 'Audio'], ['document', 'Documents']]
const ICON = { image: '🖼', video: '🎬', audio: '🎵', document: '📄', other: '📦' }
const THUMB_KINDS = new Set(['image', 'video'])

function readPref(key, fallback) {
  try { return localStorage.getItem(key) || fallback } catch { return fallback }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value) } catch { /* storage unavailable */ }
}

function Thumb({ f }) {
  const [failed, setFailed] = useState(false)
  const canThumb = (THUMB_KINDS.has(f.kind) || f.filename.toLowerCase().endsWith('.pdf')) && !failed
  return (
    <div className="thumb">
      {canThumb
        ? <img src={`/api/files/${f.id}/thumbnail`} alt="" loading="lazy" onError={() => setFailed(true)} />
        : <span className="thumb-icon">{ICON[f.kind] || ICON.other}</span>}
      {f.kind === 'video' && <span className="thumb-play">▶</span>}
      {['critical', 'precious'].includes(f.value_tier) && <span className={`tag tier-${f.value_tier} thumb-tier`}>{f.value_tier}</span>}
    </div>
  )
}

export default function Files() {
  const [data, setData]       = useState(null)
  const [page, setPage]       = useState(0)
  const [query, setQuery]     = useState('')
  const [q, setQ]             = useState('')         // debounced
  const [kind, setKind]       = useState('')
  const [filter, setFilter]   = useState('all')
  const [view, setView]       = useState(() => readPref('dam.files.view', 'grid'))
  const [err, setErr]         = useState(null)
  const [busy, setBusy]       = useState(false)
  const [viewing, setViewing] = useState(null)       // index into the list being viewed
  const [viewList, setViewList] = useState(null)     // map points opened in the viewer
  const ai = useAIStatus()
  const semanticReady = ai.status?.semantic_enabled && ai.status?.state === 'ready'
  const reqId = useRef(0)

  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300)
    return () => clearTimeout(t)
  }, [query])

  const load = useCallback(async (p) => {
    const id = ++reqId.current
    setBusy(true)
    try {
      let d
      if (q) {
        d = await api.search({ q, mode: 'auto', limit: 100, ...(kind && { kind }) })
        d = { files: d.results, total: d.results.length, mode: d.mode, notice: d.notice }
      } else {
        d = await api.files({
          limit: PAGE, offset: p * PAGE,
          ...(kind && { kind }),
          ...(filter === 'duplicates' && { duplicates_only: true }),
          ...(filter === 'marked' && { marked_only: true }),
        })
      }
      if (id === reqId.current) { setData(d); setErr(null) }
    } catch (e) {
      if (id === reqId.current) setErr(e.message)
    } finally {
      if (id === reqId.current) setBusy(false)
    }
  // Re-run searches when semantic search becomes ready so results upgrade from keyword
  }, [q, kind, filter, semanticReady])

  useEffect(() => { setPage(0); load(0) }, [load])
  useEffect(() => { if (page !== 0) load(page) }, [page])  // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMark = async (id, mark) => {
    try { await api.markFile(id, mark); load(page) } catch (e) { setErr(e.message) }
  }

  const changeView = v => { setView(v); writePref('dam.files.view', v) }

  const rows = data?.files || []
  const pages = data && !q ? Math.ceil(data.total / PAGE) : 0
  const searching = !!q

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Files</div>
          <div className="section-sub">
            {data && (searching
              ? `${rows.length} result${rows.length === 1 ? '' : 's'} · ${data.mode === 'hybrid' ? 'semantic + keyword' : data.mode}`
              : `${data.total.toLocaleString()} files`)}
          </div>
        </div>
        <SemanticToggle status={ai.status} onChange={ai.setStatus} />
      </div>

      {ai.status?.semantic_enabled && ai.status.state === 'loading' && (
        <div className="info-box"><span className="spinner sm" /> Loading the embedding model on your Mac — keyword results are shown until it’s ready.</div>
      )}
      {searching && data?.notice && !(ai.status?.state === 'loading') && <div className="info-box">{data.notice}</div>}
      {err && <div className="error-box">{err}</div>}

      <div className="files-toolbar">
        <input
          className="search"
          style={{ width: 340 }}
          placeholder={semanticReady ? 'Describe what you’re looking for…' : 'Search names, folders, metadata…'}
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <div className="chip-row" style={{ marginTop: 0 }}>
          {KINDS.map(([value, label]) => (
            <button key={label} className={`chip${kind === value ? ' active' : ''}`} onClick={() => setKind(value)}>{label}</button>
          ))}
        </div>
        {!searching && (
          <div className="chip-row" style={{ marginTop: 0 }}>
            {[['all', 'Any'], ['duplicates', 'Duplicates'], ['marked', 'Marked']].map(([value, label]) => (
              <button key={value} className={`chip${filter === value ? ' active' : ''}`} onClick={() => setFilter(value)}>{label}</button>
            ))}
          </div>
        )}
        <span style={{ flex: 1 }} />
        {busy && <div className="spinner sm" />}
        <div className="view-toggle">
          <button className={`btn sm${view === 'grid' ? ' primary' : ''}`} onClick={() => changeView('grid')}>Grid</button>
          <button className={`btn sm${view === 'list' ? ' primary' : ''}`} onClick={() => changeView('list')}>List</button>
          <button className={`btn sm${view === 'map' ? ' primary' : ''}`} onClick={() => changeView('map')} title="Similarity map">Map</button>
        </div>
      </div>

      {view === 'map' && (
        <SimilarityMap
          kind={kind}
          onOpen={(points, i) => {
            setViewList(points.map(p => ({ id: p.file_id, filename: p.filename, filesize: p.filesize, kind: p.kind, value_tier: p.tier })))
            setViewing(i)
          }}
        />
      )}

      {view !== 'map' && data && rows.length === 0 && (
        <div className="card cfg-empty" style={{ textAlign: 'center', padding: 40 }}>
          {searching ? 'Nothing matched.' : 'No files yet. Drop files in the hot folder or run a scan.'}
        </div>
      )}

      {view === 'grid' && rows.length > 0 && (
        <div className="grid">
          {rows.map((f, i) => (
            <button key={f.id} className={`grid-card${f.marked_for_deletion ? ' marked' : ''}`} onClick={() => setViewing(i)} title={f.filepath}>
              <Thumb f={f} />
              <div className="grid-name">{f.filename}</div>
              <div className="grid-sub">
                {fmtBytes(f.filesize)}
                {f.score != null && ` · ${Math.round(f.score * 100)}% match`}
              </div>
            </button>
          ))}
        </div>
      )}

      {view === 'list' && rows.length > 0 && (
        <div className="files-table-wrap">
          <table className="files-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Path</th>
                <th style={{ textAlign: 'right' }}>Size</th>
                <th>{searching ? 'Match' : 'Added'}</th>
                <th>Tier</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((f, i) => (
                <tr key={f.id} className={f.marked_for_deletion ? 'row-marked' : ''}>
                  <td className="cell-name">{ICON[f.kind] || ICON.other} {f.filename}</td>
                  <td className="cell-path">{f.filepath}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtBytes(f.filesize)}</td>
                  <td style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)', fontSize: 12 }}>
                    {searching
                      ? (f.score != null ? `${Math.round(f.score * 100)}%` : 'keyword')
                      : f.scan_date && new Date(f.scan_date).toLocaleDateString()}
                  </td>
                  <td>{f.value_tier && <span className={`tag tier-${f.value_tier}`}>{f.value_tier}</span>}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn sm" style={{ marginRight: 6 }} onClick={() => setViewing(i)}>View</button>
                    <button className={`btn sm${f.marked_for_deletion ? '' : ' danger'}`} onClick={() => toggleMark(f.id, !f.marked_for_deletion)}>
                      {f.marked_for_deletion ? 'Unmark' : 'Mark'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {view !== 'map' && pages > 1 && (
        <div className="pagination">
          <button className="btn sm" disabled={page === 0} onClick={() => setPage(0)}>«</button>
          <button className="btn sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>‹</button>
          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{page + 1} / {pages}</span>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)}>›</button>
          <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(pages - 1)}>»</button>
        </div>
      )}

      {viewing !== null && (viewList || rows)[viewing] && (
        <Viewer files={viewList || rows} index={viewing} onIndex={setViewing}
                onClose={() => { setViewing(null); setViewList(null) }} />
      )}
    </>
  )
}
