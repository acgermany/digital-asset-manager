import { useState, useEffect, useCallback } from 'react'

const BATCH_LABEL = {
  queued: 'Queued', running: 'Running', cancelling: 'Stopping…', done: 'Finished',
  cancelled: 'Cancelled', failed: 'Failed',
}

// Coverage + batch controls for a long-running AI job on the Mac (descriptions, transcripts).
// `spec` supplies the API calls and the words; everything else is shared.
export default function BatchPanel({ spec, onConfigure }) {
  const [s, setS]     = useState(null)
  const [err, setErr] = useState(null)

  const load = useCallback(async () => {
    try { setS(await spec.status()); setErr(null) } catch (e) { setErr(e.message) }
  }, [spec])

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
  const done = cov[spec.doneKey]
  const pct = cov.eligible ? Math.round(done / cov.eligible * 100) : 0
  const b = s.batch
  const bp = b?.progress || {}
  const state = spec.state(s)

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">{spec.title}</div>
          <div className="section-sub">{spec.sub(s)}</div>
        </div>
        <span className={`semantic-state ${state.tone}`}>{state.label}</span>
      </div>
      {err && <div className="error-box">{err}</div>}
      {s.error && <div className="warn-box">{s.error}<button className="btn sm" onClick={onConfigure}>AI settings</button></div>}

      <div className="card cfg-section">
        <div className="cfg-section-head">
          <div>
            <div className="cfg-section-title">Coverage</div>
            <div className="cfg-section-sub">
              {done.toLocaleString()} of {cov.eligible.toLocaleString()} {spec.noun} {spec.doneWord}
              {spec.extra?.(cov)}
              {cov.failed > 0 && ` · ${cov.failed} couldn’t be read`}
              {s.auto && ' · new files are handled automatically'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            {running
              ? <button className="btn sm danger" onClick={() => act(spec.cancel)}>Stop</button>
              : <>
                  <button className="btn sm primary" disabled={!cov.pending} onClick={() => act(() => spec.start(false))}>
                    {spec.startLabel} {cov.pending.toLocaleString()} pending
                  </button>
                  <button className="btn sm" disabled={!cov.eligible} onClick={() => act(() => spec.start(true))} title={spec.redoHelp}>
                    {spec.redoLabel}
                  </button>
                </>}
          </div>
        </div>
        <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
        <div className="cfg-help">{spec.help}</div>
      </div>

      {b && (
        <div className="card cfg-section">
          <div className="cfg-section-title">Batch #{b.id} · {BATCH_LABEL[b.status] || b.status}</div>
          {bp.total > 0 && (
            <>
              <div className="progress-track"><div className="progress-fill" style={{ width: `${Math.round(bp.done / bp.total * 100)}%` }} /></div>
              <div className="cfg-muted">
                {bp.done} / {bp.total} · {spec.progress(bp)}{bp.failed ? ` · ${bp.failed} unreadable` : ''}
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
