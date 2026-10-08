import { useState } from 'react'
import { api } from '../api'
import { useAIStatus } from '../hooks'
import SemanticToggle from './SemanticToggle'

const BATCH_LABEL = {
  queued: 'Queued', running: 'Running', cancelling: 'Stopping…', done: 'Finished',
  cancelled: 'Cancelled', failed: 'Failed',
}

export default function Embeddings({ onConfigure }) {
  const ai = useAIStatus()
  const [err, setErr] = useState(null)
  const s = ai.status

  const act = async (fn) => {
    setErr(null)
    try { ai.setStatus(await fn()) } catch (e) { setErr(e.message) }
  }

  if (!s) return ai.err ? <div className="error-box">{ai.err}</div> : <div className="cfg-muted">Loading…</div>

  const cov = s.coverage
  const pct = cov.total ? Math.round(cov.embedded / cov.total * 100) : 0
  const batch = s.batch
  const running = batch && ['queued', 'running', 'cancelling'].includes(batch.status)
  const bp = batch?.progress || {}

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Embeddings</div>
          <div className="section-sub">Vectors for semantic search, made by <code>{s.model}</code> on your Mac</div>
        </div>
        <SemanticToggle status={s} onChange={ai.setStatus} />
      </div>
      {err && <div className="error-box">{err}</div>}
      {!s.reachable && (
        <div className="warn-box">
          Ollama isn’t reachable. Embedding batches wait until it is.
          <button className="btn sm" onClick={onConfigure}>Check AI settings</button>
        </div>
      )}

      <div className="card cfg-section">
        <div className="cfg-section-head">
          <div>
            <div className="cfg-section-title">Coverage</div>
            <div className="cfg-section-sub">{cov.embedded.toLocaleString()} of {cov.total.toLocaleString()} assets embedded</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {running
              ? <button className="btn sm danger" onClick={() => act(api.cancelEmbed)}>Stop batch</button>
              : <>
                  <button className="btn sm primary" disabled={!cov.pending} onClick={() => act(() => api.startEmbed(false))}>
                    Embed {cov.pending.toLocaleString()} pending
                  </button>
                  <button className="btn sm" disabled={!cov.total} onClick={() => act(() => api.startEmbed(true))}
                          title="Rebuild every embedding (after changing the model or the text template)">
                    Re-embed all
                  </button>
                </>}
          </div>
        </div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
        <div className="cfg-help">
          Critical and precious items are embedded first. While semantic search is on, new files are embedded automatically in the background.
          {Object.keys(cov.other_models || {}).length > 0 && ` Also stored: ${Object.entries(cov.other_models).map(([m, n]) => `${m} (${n})`).join(', ')}.`}
        </div>
      </div>

      {batch && (
        <div className="card cfg-section">
          <div className="cfg-section-head">
            <div>
              <div className="cfg-section-title">{batch.label || 'Batch'} #{batch.id}</div>
              <div className="cfg-section-sub">
                {BATCH_LABEL[batch.status] || batch.status}
                {batch.created_at && ` · started ${new Date(batch.created_at).toLocaleString()}`}
              </div>
            </div>
          </div>
          {bp.total > 0 && (
            <>
              <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.round(bp.done / bp.total * 100)}%` }} /></div>
              <div className="cfg-muted">{bp.done} / {bp.total} processed{bp.running ? ` · ${bp.running} running` : ''}{bp.failed ? ` · ${bp.failed} failed` : ''} (unchanged items are skipped)</div>
            </>
          )}
          {batch.waiting_reason && running && <div className="cfg-help">⏸ {batch.waiting_reason}</div>}
          {batch.error && batch.status === 'failed' && <div className="share-err" style={{ paddingLeft: 0 }}>{batch.error}</div>}
        </div>
      )}
    </>
  )
}
