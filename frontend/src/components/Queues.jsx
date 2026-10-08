import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

function ago(iso) {
  if (!iso) return '—'
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

const WORKER_TONE = { running: 'ok', draining: 'warn', offline: 'bad', stopped: 'idle' }
const BATCH_TONE = { running: 'warn', cancelling: 'warn', done: 'ok', cancelled: 'idle' }

function QueueRow({ q, onChange, onError }) {
  const act = async (fn) => { try { await fn(); onChange() } catch (e) { onError(e.message) } }
  const capped = q.max_running != null
  const atCap = capped && q.running >= q.max_running
  return (
    <tr className={q.enabled ? '' : 'row-marked'}>
      <td>
        <div className="cell-name">{q.name}</div>
        <div className="cell-sub" style={{ fontFamily: 'inherit' }} title={q.description}>{q.description}</div>
        {q.waiting_reason && <div className="queue-wait" title={q.waiting_reason}>⏸ {q.waiting_reason}</div>}
      </td>
      <td>
        <span className={`semantic-state ${q.enabled ? 'ok' : 'warn'}`}>{q.enabled ? 'Active' : 'Paused'}</span>
      </td>
      <td className="num">{q.ready.toLocaleString()}{q.waiting ? <div className="cfg-muted">+{q.waiting} waiting</div> : null}</td>
      <td className="num">
        <span className={atCap ? 'at-cap' : ''}>{q.running}{capped ? ` / ${q.max_running}` : ''}</span>
        <div className="cfg-muted">{q.consumers} slot{q.consumers === 1 ? '' : 's'}</div>
      </td>
      <td className="num">{q.done_1h.toLocaleString()}</td>
      <td className="num" style={q.failed_24h ? { color: 'var(--red)' } : undefined}>{q.failed_24h}</td>
      <td style={{ whiteSpace: 'nowrap', textAlign: 'right' }}>
        <button className="btn sm" onClick={() => act(() => api.updateQueue(q.name, { enabled: !q.enabled }))}>
          {q.enabled ? 'Pause' : 'Resume'}
        </button>
        {q.failed_24h > 0 && <button className="btn sm" onClick={() => act(() => api.retryFailed(q.name))}>Retry failed</button>}
        {q.ready + q.waiting > 0 && (
          <button className="btn sm danger" onClick={() => {
            if (window.confirm(`Cancel ${q.ready + q.waiting} queued job(s) in “${q.name}”? Running jobs finish.`)) act(() => api.clearQueue(q.name))
          }}>Clear</button>
        )}
      </td>
    </tr>
  )
}

function WorkerCard({ w, allQueues, onChange, onError }) {
  const act = async (fn) => { try { await fn(); onChange() } catch (e) { onError(e.message) } }
  const queues = w.desired_queues ?? w.queues
  const conc = w.desired_concurrency ?? w.concurrency
  const pending = (w.desired_concurrency != null && w.desired_concurrency !== w.concurrency) ||
                  (w.desired_queues != null && JSON.stringify(w.desired_queues) !== JSON.stringify(w.queues))
  const live = w.status === 'running' || w.status === 'draining'
  const toggleQueue = q => act(() => api.updateWorker(w.id, { queues: queues.includes(q) ? queues.filter(x => x !== q) : [...queues, q] }))

  return (
    <div className="card worker-card">
      <div className="worker-head">
        <div>
          <div className="worker-name">{w.name} {w.embedded && <span className="tag grey">in app</span>}</div>
          <div className="cfg-muted mono">{w.hostname} · v{w.version} · seen {ago(w.last_seen)}</div>
        </div>
        <span className={`semantic-state ${WORKER_TONE[w.status] || 'idle'}`}>{w.status}</span>
      </div>
      <div className="worker-stats">
        <span><strong>{w.running}</strong> running</span>
        <span><strong>{w.processed.toLocaleString()}</strong> done</span>
        <span style={w.failed ? { color: 'var(--red)' } : undefined}><strong>{w.failed}</strong> failed</span>
      </div>
      <div className="worker-row">
        <span className="cfg-muted">Jobs at once</span>
        <span className="stepper">
          <button className="btn sm" disabled={!live || conc <= 0} onClick={() => act(() => api.updateWorker(w.id, { concurrency: conc - 1 }))}>−</button>
          <strong>{conc}</strong>
          <button className="btn sm" disabled={!live || conc >= 32} onClick={() => act(() => api.updateWorker(w.id, { concurrency: conc + 1 }))}>+</button>
        </span>
      </div>
      <div className="worker-row" style={{ alignItems: 'flex-start' }}>
        <span className="cfg-muted">Queues</span>
        <span className="chip-row" style={{ marginTop: 0, justifyContent: 'flex-end' }}>
          {allQueues.map(q => (
            <button key={q} className={`chip${queues.includes(q) ? ' active' : ''}`} disabled={!live} onClick={() => toggleQueue(q)}>{q}</button>
          ))}
        </span>
      </div>
      {pending && <div className="cfg-help">Applying on the worker’s next heartbeat…</div>}
      <div className="worker-actions">
        {w.status === 'running' && <button className="btn sm" onClick={() => act(() => api.updateWorker(w.id, { status: 'draining' }))} title="Finish current jobs, take no new ones">Drain</button>}
        {w.status === 'draining' && <button className="btn sm" onClick={() => act(() => api.updateWorker(w.id, { status: 'running' }))}>Resume</button>}
        {(w.desired_concurrency != null || w.desired_queues != null) && live &&
          <button className="btn sm" onClick={() => act(() => api.updateWorker(w.id, { reset: true }))} title="Go back to the worker's own settings (env vars)">Reset overrides</button>}
        {!live && <button className="btn sm danger" onClick={() => act(() => api.forgetWorker(w.id))}>Remove</button>}
      </div>
    </div>
  )
}

export default function Queues() {
  const [queues, setQueues]   = useState(null)
  const [workers, setWorkers] = useState(null)
  const [batches, setBatches] = useState([])
  const [err, setErr]         = useState(null)
  const [notice, setNotice]   = useState(null)

  const load = useCallback(async () => {
    try {
      const [q, w, b] = await Promise.all([api.queues(), api.workers(), api.batches()])
      setQueues(q.queues); setWorkers(w); setBatches(b.batches); setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [load])

  const render = async () => {
    try {
      const b = await api.startRender(['image', 'video'])
      setNotice(`Queued ${b.progress.total.toLocaleString()} items to render`)
      setTimeout(() => setNotice(null), 4000)
      load()
    } catch (e) { setErr(e.message) }
  }

  const cancel = async (id) => { try { await api.cancelBatch(id); load() } catch (e) { setErr(e.message) } }

  if (!queues || !workers) return err ? <div className="error-box">{err}</div> : <div className="cfg-muted">Loading…</div>

  const liveWorkers = workers.workers.filter(w => w.status === 'running' || w.status === 'draining')
  const slots = liveWorkers.filter(w => w.status === 'running').reduce((a, w) => a + w.concurrency, 0)

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Queues</div>
          <div className="section-sub">
            {liveWorkers.length} worker{liveWorkers.length === 1 ? '' : 's'} · {slots} job slot{slots === 1 ? '' : 's'} · limits are set in Configure → Processing
          </div>
        </div>
        {notice && <span className="cfg-notice">{notice}</span>}
      </div>
      {err && <div className="error-box">{err}</div>}

      <div className="files-table-wrap" style={{ marginBottom: 20 }}>
        <table className="files-table queue-table">
          <thead>
            <tr><th>Queue</th><th>State</th><th className="num">Queued</th><th className="num">Running</th><th className="num">Done / hr</th><th className="num">Failed 24h</th><th /></tr>
          </thead>
          <tbody>
            {queues.map(q => <QueueRow key={q.name} q={q} onChange={load} onError={setErr} />)}
          </tbody>
        </table>
      </div>

      <div className="section-header">
        <div>
          <div className="section-title">Workers</div>
          <div className="section-sub">Changes apply live. Add more with <code>docker compose up -d --scale worker=N</code> (see Configure → Processing).</div>
        </div>
      </div>
      {liveWorkers.length === 0 && <div className="warn-box">No workers are running — queued jobs won’t be processed.</div>}
      <div className="worker-grid">
        {workers.workers.map(w => <WorkerCard key={w.id} w={w} allQueues={workers.all_queues} onChange={load} onError={setErr} />)}
      </div>

      <div className="section-header" style={{ marginTop: 20 }}>
        <div>
          <div className="section-title">Batches</div>
          <div className="section-sub">Runs over many items; each item is its own job, shared across all workers</div>
        </div>
        <button className="btn sm primary" onClick={render} title="Build thumbnails, display images and browser-playable video proxies ahead of time">
          Pre-render previews
        </button>
      </div>
      {batches.length === 0 && <div className="card cfg-empty">No batches yet.</div>}
      {batches.map(b => {
        const p = b.progress
        const pct = p.total ? Math.round(p.done / p.total * 100) : 100
        const outcomes = Object.entries(p).filter(([k]) => !['total', 'done', 'queued', 'running', 'failed', 'cancelled', 'current'].includes(k))
        return (
          <div key={b.id} className="card batch-row">
            <div className="batch-head">
              <span className="cell-name">{b.label || b.kind} <span className="cfg-muted">#{b.id} · {b.kind}{b.created_by !== 'user' ? ` · ${b.created_by}` : ''}</span></span>
              <span className={`semantic-state ${BATCH_TONE[b.status] || 'idle'}`}>{b.status}</span>
              {b.status === 'running' && <button className="btn sm danger" onClick={() => cancel(b.id)}>Cancel</button>}
            </div>
            <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
            <div className="cfg-muted">
              {p.done.toLocaleString()} / {p.total.toLocaleString()}
              {p.running ? ` · ${p.running} running` : ''}{p.queued ? ` · ${p.queued.toLocaleString()} queued` : ''}
              {outcomes.map(([k, v]) => ` · ${v} ${k.replace(/_/g, ' ')}`).join('')}
              {p.failed ? ` · ${p.failed} failed` : ''}{p.cancelled ? ` · ${p.cancelled} cancelled` : ''}
              {p.current && b.status === 'running' ? ` · now: ${p.current}` : ''}
              {' · started '}{ago(b.created_at)}
            </div>
            {b.waiting_reason && b.status === 'running' && <div className="cfg-help">⏸ {b.waiting_reason}</div>}
          </div>
        )
      })}
    </>
  )
}
