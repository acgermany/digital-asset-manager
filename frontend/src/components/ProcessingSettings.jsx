import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

const HINT = {
  ingest: 'Disk/NAS bound. Unlimited is fine; slots on workers set the real pace.',
  scan: 'One scan at a time keeps the disks from thrashing.',
  render: 'CPU bound (ffmpeg). Each running proxy uses ~0.5 GB RAM on the worker.',
  embed: 'Bound by the Mac. 1–2 is plenty.',
  vision: 'Bound by the Mac’s GPU. Raise only if Ollama runs with OLLAMA_NUM_PARALLEL > 1.',
  speech: 'Bound by the Mac’s GPU (Whisper). Usually 1.',
}

function CopyLine({ cmd }) {
  const [done, setDone] = useState(false)
  return (
    <div className="copy-line">
      <code>{cmd}</code>
      <button className="btn sm" onClick={async () => {
        try { await navigator.clipboard.writeText(cmd); setDone(true); setTimeout(() => setDone(false), 1200) } catch { /* no clipboard */ }
      }}>{done ? 'Copied' : 'Copy'}</button>
    </div>
  )
}

function QueueSettingsRow({ q, save }) {
  return (
    <tr>
      <td>
        <div className="cell-name">{q.name}</div>
        <div className="cfg-muted" style={{ maxWidth: 360 }}>{HINT[q.name] || q.description}</div>
      </td>
      <td>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={q.enabled} onChange={e => save(q.name, { enabled: e.target.checked })} /> On
        </label>
      </td>
      <td>
        <span className="cfg-num">
          <input className="search" type="number" min="0" max="256" style={{ width: 70 }}
                 disabled={q.max_running == null} key={`${q.name}-${q.max_running}`}
                 defaultValue={q.max_running ?? ''}
                 onBlur={e => e.target.value !== '' && Number(e.target.value) !== q.max_running && save(q.name, { max_running: Number(e.target.value) })} />
          <label className="cfg-toggle" style={{ marginTop: 0, fontSize: 12 }}>
            <input type="checkbox" checked={q.max_running == null}
                   onChange={e => save(q.name, e.target.checked ? { unlimited: true } : { max_running: Math.max(1, q.running || 1) })} /> no limit
          </label>
        </span>
      </td>
      <td>
        <input className="search" type="number" min="1" max="100" style={{ width: 64 }} key={`${q.name}-a-${q.max_attempts}`}
               defaultValue={q.max_attempts}
               onBlur={e => Number(e.target.value) !== q.max_attempts && save(q.name, { max_attempts: Number(e.target.value) })} />
      </td>
      <td>
        <span className="cfg-num">
          <input className="search" type="number" min="1" max="1440" style={{ width: 64 }} key={`${q.name}-s-${q.stall_seconds}`}
                 defaultValue={Math.round(q.stall_seconds / 60)}
                 onBlur={e => Number(e.target.value) * 60 !== q.stall_seconds && save(q.name, { stall_seconds: Number(e.target.value) * 60 })} />
          min
        </span>
      </td>
    </tr>
  )
}

export default function ProcessingSettings({ notify, onError }) {
  const [queues, setQueues] = useState(null)

  const load = useCallback(async () => {
    try { setQueues((await api.queues()).queues) } catch (e) { onError(e.message) }
  }, [onError])
  useEffect(() => { load() }, [load])

  const save = async (name, changes) => {
    try {
      setQueues((await api.updateQueue(name, changes)).queues)
      notify(`Queue “${name}” updated`)
    } catch (e) {
      onError(e.message)
    }
  }

  if (!queues) return <div className="cfg-muted">Loading…</div>

  return (
    <>
      <div className="files-table-wrap" style={{ marginBottom: 14 }}>
        <table className="files-table">
          <thead>
            <tr>
              <th>Queue</th><th>Enabled</th>
              <th title="Most jobs from this queue running at once across ALL workers">Max running (all workers)</th>
              <th title="Attempts before a job is marked failed">Retries</th>
              <th title="A running job whose worker stops reporting for this long is given to another worker">Stall timeout</th>
            </tr>
          </thead>
          <tbody>{queues.map(q => <QueueSettingsRow key={q.name} q={q} save={save} />)}</tbody>
        </table>
      </div>
      <div className="cfg-help" style={{ marginBottom: 12 }}>
        <strong>How pace works:</strong> each worker runs a number of jobs at once (its slots), taking from the queues it’s assigned.
        “Max running” caps a queue across every worker, so adding workers speeds up CPU work (ingest, render) but never overloads the Mac.
        Per-worker slots and queues are changed live in Workflows → Queues.
      </div>

      <div className="cfg-subhead">Adding workers</div>
      <div className="cfg-help" style={{ marginTop: 0 }}>On the Linux box (same storage, nothing else to set up):</div>
      <CopyLine cmd="docker compose up -d --scale worker=3" />
      <div className="cfg-help">Only certain queues, more slots each:</div>
      <CopyLine cmd="WORKER_QUEUES=render WORKER_CONCURRENCY=4 docker compose up -d --scale worker=2" />
      <div className="cfg-help">
        On another machine: use <code>docker-compose.worker.yml</code>. It needs the same files at the same paths, the app’s
        data folder, and Postgres reachable on the LAN (<code>DB_PUBLISH=0.0.0.0:5432</code> with a real <code>DB_PASSWORD</code>). Full steps
        are in <code>docs/SCALING.md</code>.
      </div>
      <CopyLine cmd="docker compose -f docker-compose.worker.yml up -d --scale worker=2" />
    </>
  )
}
