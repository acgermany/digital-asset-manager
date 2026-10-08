import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

const BATCH_LABEL = {
  queued: 'Queued', running: 'Running', cancelling: 'Stopping…', done: 'Finished',
  cancelled: 'Cancelled', failed: 'Failed',
}

export default function Descriptions({ onConfigure }) {
  const [s, setS]     = useState(null)
  const [err, setErr] = useState(null)

  const load = useCallback(async () => {
    try { setS(await api.describeStatus()); setErr(null) } catch (e) { setErr(e.message) }
  }, [])

  const running = s?.batch && ['queued', 'running', 'cancelling'].includes(s.batch.status)
  useEffect(() => {
    load()
    const t = setInterval(load, running ? 2000 : 10000)
    return () => clearInterval(t)
  }, [load, running])

  const act = async fn => {
    setErr(null)
    try { setS(await fn()) } catch (e) { setErr(e.message) }
  }

  if (!s) return err ? <div className="error-box">{err}</div> : null
  const cov = s.coverage
  const pct = cov.eligible ? Math.round(cov.described / cov.eligible * 100) : 0
  const b = s.batch
  const bp = b?.progress || {}

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Descriptions</div>
          <div className="section-sub">Photo captions and video summaries by <code>{s.model}</code> on your Mac</div>
        </div>
        <span className={`semantic-state ${s.model_installed ? (s.model_loaded ? 'ok' : 'idle') : 'bad'}`}>
          {!s.reachable ? 'Mac offline' : !s.model_installed ? 'Model not installed' : s.model_loaded ? 'Model loaded' : 'Model not loaded'}
        </span>
      </div>
      {err && <div className="error-box">{err}</div>}
      {s.error && (
        <div className="warn-box">{s.error}<button className="btn sm" onClick={onConfigure}>AI settings</button></div>
      )}

      <div className="card cfg-section">
        <div className="cfg-section-head">
          <div>
            <div className="cfg-section-title">Coverage</div>
            <div className="cfg-section-sub">
              {cov.described.toLocaleString()} of {cov.eligible.toLocaleString()} images and videos described
              {cov.failed > 0 && ` · ${cov.failed} couldn’t be read`}
              {s.auto && ' · new files are described automatically'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {running
              ? <button className="btn sm danger" onClick={() => act(api.cancelDescribe)}>Stop</button>
              : <>
                  <button className="btn sm primary" disabled={!cov.pending} onClick={() => act(() => api.startDescribe(false))}>
                    Describe {cov.pending.toLocaleString()} pending
                  </button>
                  <button className="btn sm" disabled={!cov.eligible} onClick={() => act(() => api.startDescribe(true))}
                          title="Redo every description (after changing the model or prompts)">
                    Re-describe all
                  </button>
                </>}
          </div>
        </div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
        <div className="cfg-help">
          Precious items go first. Roughly 5–10 seconds per photo and 20–40 seconds per video once the model is loaded.
          New descriptions are re-embedded so search and the map pick them up.
        </div>
      </div>

      {b && (
        <div className="card cfg-section">
          <div className="cfg-section-title">Batch #{b.id} · {BATCH_LABEL[b.status] || b.status}</div>
          {bp.total > 0 && (
            <>
              <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.round(bp.done / bp.total * 100)}%` }} /></div>
              <div className="cfg-muted">
                {bp.done} / {bp.total} · {bp.described} described{bp.failed ? ` · ${bp.failed} unreadable` : ''}
                {bp.current && running && ` · now: ${bp.current}`}
              </div>
            </>
          )}
          {b.waiting_reason && running && <div className="cfg-help">⏸ {b.waiting_reason}</div>}
          {b.error && b.status === 'failed' && <div className="share-err" style={{ paddingLeft: 0 }}>{b.error}</div>}
        </div>
      )}
    </>
  )
}
