import { useState, useEffect, useCallback } from 'react'
import { api, fmtBytes } from '../api'

const STATUS_LABEL = {
  queued: 'Queued', processing: 'Processing', accepted: 'Filed',
  duplicate_deleted: 'Duplicate removed', duplicate_review: 'Needs review', failed: 'Failed',
}
const STATUS_TONE = {
  queued: 'grey', processing: 'blue', accepted: 'green',
  duplicate_deleted: 'yellow', duplicate_review: 'purple', failed: 'red',
}
const FILTERS = [
  ['', 'All'], ['queued,processing', 'In progress'], ['accepted', 'Filed'],
  ['duplicate_deleted', 'Duplicates removed'], ['duplicate_review', 'Needs review'], ['failed', 'Failed'],
]

export default function Ingest({ onConfigure, onReview }) {
  const [data, setData]       = useState(null)
  const [health, setHealth]   = useState(null)
  const [jobs, setJobs]       = useState(null)
  const [filter, setFilter]   = useState('')
  const [err, setErr]         = useState(null)

  const load = useCallback(async () => {
    try {
      const params = { limit: 100 }
      if (filter) params.status = filter
      const [d, h, j] = await Promise.all([api.ingests(params), api.health(), api.jobsSummary()])
      setData(d); setHealth(h); setJobs(j); setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [filter])

  useEffect(() => {
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [load])

  const retry = async (id) => {
    try { await api.retryIngest(id); load() } catch (e) { setErr(e.message) }
  }

  const counts = data?.counts || {}
  const inFlight = (counts.queued || 0) + (counts.processing || 0)

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Hot folder ingest</div>
          <div className="section-sub">Live activity — refreshes every few seconds</div>
        </div>
        {inFlight > 0 && <span className="cfg-notice">{inFlight} in progress</span>}
      </div>

      {health && (!health.hotfolder_ready || !health.library_ready) && (
        <div className="warn-box">
          {!health.hotfolder_ready && <div>The hot folder isn’t set up yet.</div>}
          {!health.library_ready && <div>The library isn’t available — files will wait in the hot folder until it is.</div>}
          <button className="btn sm" onClick={onConfigure}>Open Configure</button>
        </div>
      )}
      {jobs?.waiting_reason && <div className="warn-box">Waiting: {jobs.waiting_reason}</div>}
      {err && <div className="error-box">{err}</div>}

      <div className="chip-row" style={{ marginBottom: 12 }}>
        {FILTERS.map(([value, label]) => {
          const n = value ? value.split(',').reduce((a, k) => a + (counts[k] || 0), 0)
                          : Object.values(counts).reduce((a, b) => a + b, 0)
          return (
            <button key={label} className={`chip${filter === value ? ' active' : ''}`} onClick={() => setFilter(value)}>
              {label} <span className="chip-count">{n}</span>
            </button>
          )
        })}
      </div>

      <div className="files-table-wrap">
        <table className="files-table">
          <thead>
            <tr><th>File</th><th>Provenance</th><th style={{ textAlign: 'right' }}>Size</th><th>Status</th><th>When</th><th /></tr>
          </thead>
          <tbody>
            {data?.ingests.length === 0 && (
              <tr><td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-muted)', padding: 28 }}>
                Nothing yet — drop a file into the hot folder.
              </td></tr>
            )}
            {data?.ingests.map(i => (
              <tr key={i.id}>
                <td className="cell-name">
                  {i.filename}
                  <div className="cell-sub" title={i.current_path || i.original_path}>
                    {i.status === 'accepted' ? `→ ${i.current_path}` : i.error || i.original_path}
                  </div>
                </td>
                <td>{i.provenance}</td>
                <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{fmtBytes(i.filesize)}</td>
                <td>
                  <span className={`tag ${STATUS_TONE[i.status] || 'grey'}`}>
                    {i.status === 'processing' && i.step ? `${STATUS_LABEL[i.status]} · ${i.step}` : STATUS_LABEL[i.status] || i.status}
                  </span>
                  {i.value_tier && ['critical', 'precious'].includes(i.value_tier) && (
                    <span className={`tag tier-${i.value_tier}`}>{i.value_tier}</span>
                  )}
                </td>
                <td style={{ whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                  {i.ingest_date && new Date(i.ingest_date).toLocaleString()}
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  {i.status === 'failed' && <button className="btn sm" onClick={() => retry(i.id)}>Retry</button>}
                  {i.status === 'duplicate_review' && <button className="btn sm" onClick={onReview}>Review</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
