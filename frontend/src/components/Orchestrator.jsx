import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

const gb = mb => mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB` : `${Math.round(mb)} MB`
const STATE_TAG = { loaded: 'green', loading: 'yellow', pending: 'yellow', unloading: 'red', draining: 'red' }
const POLICY_TAG = { pinned: 'blue', swappable: 'purple', external: 'grey' }

function ago(ts, now) {
  const s = Math.max(0, Math.round(now - ts))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

// Where the orchestrator is, the DAM's key, and whether the DAM's AI calls go through it.
function Connection({ cfg, routed, onSaved }) {
  const [url, setUrl]     = useState(cfg.orchestrator_url || '')
  const [key, setKey]     = useState('')
  const [token, setToken] = useState('')
  const [msg, setMsg]     = useState(null)
  const [err, setErr]     = useState(null)
  useEffect(() => setUrl(cfg.orchestrator_url || ''), [cfg.orchestrator_url])

  const save = async (changes, note) => {
    try { await api.updateConfig(changes); setErr(null); setMsg(note); setTimeout(() => setMsg(null), 3000); onSaved(); return true }
    catch (e) { setErr(e.message); return false }
  }
  const saveAll = () => {
    const changes = { orchestrator_url: url }
    if (key) changes.orchestrator_key = key
    if (token) changes.orchestrator_admin_token = token
    save(changes, 'Saved').then(ok => { if (ok) { setKey(''); setToken('') } })
  }
  const clean = url.trim().replace(/\/+$/, '')

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="section-header" style={{ marginBottom: 12 }}>
        <div>
          <div className="section-title">Connection</div>
          <div className="section-sub">The orchestrator queues every AI request from the DAM and other services, and decides which models are loaded on the inference servers.</div>
        </div>
        {msg && <span className="cfg-notice">{msg}</span>}
      </div>
      {err && <div className="error-box">{err}</div>}
      <div className="share-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12, alignItems: 'end' }}>
        <label className="cfg-muted">Orchestrator URL
          <input className="search" value={url} placeholder="http://orchestrator.local:11500" onChange={e => setUrl(e.target.value)} />
        </label>
        <label className="cfg-muted">DAM API key {cfg.orchestrator_key_set && <span className="tag green">set</span>}
          <input className="search" type="password" value={key} placeholder={cfg.orchestrator_key_set ? 'leave empty to keep' : 'from the orchestrator’s Clients page'} onChange={e => setKey(e.target.value)} />
        </label>
        <label className="cfg-muted">Admin token {cfg.orchestrator_admin_token_set && <span className="tag green">set</span>}
          <input className="search" type="password" value={token} placeholder="only if the dashboard is protected" onChange={e => setToken(e.target.value)} />
        </label>
        <div><button className="btn primary" onClick={saveAll}>Save</button></div>
      </div>
      {clean && (
        <div className="cfg-help" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 12 }}>
          <span>DAM AI traffic:</span>
          <span className={`tag ${routed.ollama ? 'green' : 'grey'}`}>Ollama {routed.ollama ? 'via orchestrator' : 'direct'}</span>
          <span className={`tag ${routed.whisper ? 'green' : 'grey'}`}>Whisper {routed.whisper ? 'via orchestrator' : 'direct'}</span>
          {(!routed.ollama || !routed.whisper) &&
            <button className="btn sm" onClick={() => save({ orchestrator_url: clean, ollama_url: clean, transcribe_url: clean },
                                                            'The DAM now sends all AI work through the orchestrator')}>
              Route DAM AI through the orchestrator
            </button>}
          <span>Search runs at priority 50, background batches at 0.</span>
        </div>
      )}
    </div>
  )
}

function Partition({ p, models }) {
  const members = models.filter(m => m.partition_id === p.id && m.installed && m.policy !== 'disabled')
  const shown = members.filter(m => m.policy === 'pinned' || m.waiting || m.running || m.state !== 'unloaded')
  const pct = p.max_mb ? Math.min(100, p.used_mb / p.max_mb * 100) : 0
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12, background: 'var(--surface2)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <strong>{p.name}</strong>{p.is_default ? <span className="tag grey">default</span> : null}
        <span style={{ flex: 1 }} />
        <span className="cfg-muted">{gb(p.used_mb)} / {gb(p.max_mb)}</span>
      </div>
      <div className="progress-track" style={{ margin: '8px 0' }}><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
      <div className="cfg-muted" style={{ marginBottom: 6 }}>
        {p.running} running · <span style={p.waiting ? { color: 'var(--yellow)' } : undefined}>{p.waiting} queued</span>
      </div>
      {shown.length === 0 && <div className="cfg-muted">Nothing loaded</div>}
      {shown.map(m => {
        const state = m.draining ? 'draining' : m.state
        return (
          <div key={m.id} style={{ display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center', padding: '4px 0', borderTop: '1px solid var(--border)' }}>
            <span className="mono" style={{ flex: '1 0 100%', fontSize: 12, overflowWrap: 'anywhere' }}>{m.name}</span>
            <span className={`tag ${POLICY_TAG[m.policy] || 'grey'}`}>{m.policy}</span>
            <span className={`tag ${STATE_TAG[state] || 'grey'}`}>{state}</span>
            {m.running > 0 && <span className="tag grey">{m.running}/{m.max_parallel} running</span>}
            {m.waiting > 0 && <span className="tag yellow">{m.waiting} waiting</span>}
          </div>
        )
      })}
    </div>
  )
}

export default function Orchestrator() {
  const [data, setData] = useState(null)
  const [cfg, setCfg]   = useState(null)
  const [err, setErr]   = useState(null)

  const load = useCallback(async () => {
    try {
      const [d, c] = await Promise.all([api.orchestratorStatus(), cfg ? Promise.resolve(null) : api.config()])
      setData(d)
      if (c) setCfg(c.settings)
      setErr(null)
    } catch (e) { setErr(e.message) }
  }, [cfg])
  const reloadConfig = async () => { try { setCfg((await api.config()).settings) } catch (e) { setErr(e.message) } }

  useEffect(() => {
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [load])

  if (!data || !cfg) return err ? <div className="error-box">{err}</div> : <div className="cfg-muted">Loading…</div>
  const s = data.status

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">AI Orchestrator</div>
          <div className="section-sub">Inference servers, memory partitions and the shared AI queue</div>
        </div>
        {data.url && <a className="btn sm" href={data.url} target="_blank" rel="noreferrer">Open full dashboard ↗</a>}
      </div>
      {err && <div className="error-box">{err}</div>}
      <Connection cfg={cfg} routed={data.routed} onSaved={() => { reloadConfig(); load() }} />

      {!data.configured && <div className="warn-box">Set the orchestrator URL above to see its status here.</div>}
      {data.error && <div className="error-box">{data.error}</div>}

      {s && <>
        <div className="stat-grid">
          <div className="card"><div className="card-title">Servers online</div><div className="card-value">{s.nodes.filter(n => n.online || n.whisper_online).length} / {s.nodes.length}</div></div>
          <div className="card"><div className="card-title">Models loaded</div><div className="card-value">{s.models.filter(m => m.state === 'loaded').length}</div></div>
          <div className="card"><div className="card-title">Queued</div><div className="card-value">{s.queue.length}</div>
            <div className="card-sub">{s.queue.filter(q => q.client === 'dam').length} from the DAM</div></div>
          <div className="card"><div className="card-title">Running</div><div className="card-value">{s.models.reduce((a, m) => a + m.running, 0)}</div></div>
        </div>

        {s.nodes.map(n => (
          <div key={n.id} className="card" style={{ marginBottom: 16 }}>
            <div className="section-header" style={{ marginBottom: 12 }}>
              <div>
                <div className="section-title">
                  <span className={`semantic-state ${n.online || n.whisper_online ? 'ok' : 'bad'}`} style={{ marginRight: 8 }}>{n.online || n.whisper_online ? 'online' : 'offline'}</span>
                  {n.name}
                </div>
                <div className="section-sub mono">{n.ollama_url}{n.whisper_url ? ` · ${n.whisper_url}` : ''}</div>
              </div>
              <span className="cfg-muted">{gb(n.used_mb)} of {gb(n.budget_mb)} in use{n.oversubscribed ? ' · partitions exceed the budget' : ''}</span>
            </div>
            {n.error && <div className="error-box">{n.error}</div>}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
              {n.partitions.map(p => <Partition key={p.id} p={p} models={s.models} />)}
            </div>
          </div>
        ))}

        <div className="section-header" style={{ marginTop: 8 }}>
          <div><div className="section-title">Queue</div><div className="section-sub">Highest priority first, then oldest. Reorder or cancel in the full dashboard.</div></div>
        </div>
        {s.queue.length === 0 ? <div className="card cfg-muted" style={{ marginBottom: 16 }}>Nothing is waiting.</div> : (
          <div className="files-table-wrap" style={{ marginBottom: 16 }}>
            <table className="files-table">
              <thead><tr><th className="num">Priority</th><th>Model</th><th>Client</th><th>Endpoint</th><th className="num">Waiting</th></tr></thead>
              <tbody>{s.queue.slice(0, 20).map(q => (
                <tr key={q.id}><td className="num">{q.priority}</td><td className="mono">{q.model}</td><td>{q.client}</td><td className="mono">{q.endpoint}</td><td className="num">{Math.round(q.waited_s)}s</td></tr>
              ))}</tbody>
            </table>
            {s.queue.length > 20 && <div className="cfg-help">…and {s.queue.length - 20} more</div>}
          </div>
        )}

        <div className="section-header"><div><div className="section-title">Recent decisions</div></div></div>
        <div className="files-table-wrap">
          <table className="files-table">
            <thead><tr><th>When</th><th>Server</th><th>Model</th><th>What</th><th>Detail</th></tr></thead>
            <tbody>{s.events.slice(0, 15).map(e => (
              <tr key={e.id} style={e.level !== 'info' ? { color: e.level === 'error' ? 'var(--red)' : 'var(--yellow)' } : undefined}>
                <td>{ago(e.ts, s.now)}</td><td>{e.node || ''}</td><td className="mono">{e.model || ''}</td><td>{e.action}</td><td>{e.message}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </>}
    </>
  )
}
