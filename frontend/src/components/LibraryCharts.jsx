import { useState, useEffect, useRef } from 'react'
import { api, fmtBytes } from '../api'

// Fixed slot order (validated for the dark surface): color follows the kind, never its rank.
export const KIND_ORDER = ['image', 'video', 'audio', 'document', 'other']
export const KIND_LABEL = { image: 'Images', video: 'Videos', audio: 'Audio', document: 'Documents', other: 'Other' }
const KIND_VAR = { image: 'var(--series-1)', video: 'var(--series-2)', audio: 'var(--series-3)', document: 'var(--series-4)', other: 'var(--series-5)' }
const MAX_GROUPS = 10

function Tooltip({ tip }) {
  if (!tip) return null
  return (
    <div className="viz-tooltip" style={{ left: tip.x, top: tip.y }}>
      {tip.lines.map((l, i) => <div key={i} className={i === 0 ? 'viz-tooltip-title' : ''}>{l}</div>)}
    </div>
  )
}

function Legend({ kinds }) {
  return (
    <div className="viz-legend">
      {kinds.map(k => (
        <span key={k} className="viz-legend-item"><span className="viz-swatch" style={{ background: KIND_VAR[k] }} />{KIND_LABEL[k]}</span>
      ))}
    </div>
  )
}

function foldGroups(groups) {
  if (groups.length <= MAX_GROUPS) return groups
  const head = groups.slice(0, MAX_GROUPS - 1)
  const rest = groups.slice(MAX_GROUPS - 1)
  const other = { group: `Other (${rest.length})`, count: 0, bytes: 0, kinds: {} }
  for (const g of rest) {
    other.count += g.count
    other.bytes += g.bytes
    for (const [k, v] of Object.entries(g.kinds)) {
      const o = other.kinds[k] || (other.kinds[k] = { count: 0, bytes: 0 })
      o.count += v.count
      o.bytes += v.bytes
    }
  }
  return [...head, other]
}

function StorageBars({ groups, onTip }) {
  const rows = foldGroups(groups)
  const max = Math.max(...rows.map(r => r.bytes), 1)
  const present = KIND_ORDER.filter(k => rows.some(r => r.kinds[k]))
  return (
    <>
      <Legend kinds={present} />
      <div className="hbars">
        {rows.map(r => (
          <div key={r.group} className="hbar-row">
            <span className="hbar-label" title={r.group}>{r.group}</span>
            <div className="hbar-track">
              <div className="hbar" style={{ width: `${Math.max(r.bytes / max * 100, 0.8)}%` }}>
                {KIND_ORDER.filter(k => r.kinds[k]).map(k => (
                  <span
                    key={k}
                    className="hbar-seg"
                    style={{ flexGrow: Math.max(r.kinds[k].bytes, 1), background: KIND_VAR[k] }}
                    onMouseMove={e => onTip({ x: e.clientX, y: e.clientY, lines: [r.group, `${KIND_LABEL[k]}: ${r.kinds[k].count.toLocaleString()} files`, fmtBytes(r.kinds[k].bytes)] })}
                    onMouseLeave={() => onTip(null)}
                  />
                ))}
              </div>
              <span className="hbar-value">{fmtBytes(r.bytes)} · {r.count.toLocaleString()}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

function Timeline({ timeline, onTip }) {
  // Continuous axis: empty months/years get a zero column instead of disappearing.
  const [y0, m0] = timeline[0].month.split('-').map(Number)
  const [y1, m1] = timeline[timeline.length - 1].month.split('-').map(Number)
  const span = (y1 - y0) * 12 + (m1 - m0) + 1
  const byYear = span > 36   // many months -> roll up to years so columns stay readable
  const counts = new Map()
  for (const t of timeline) {
    const key = byYear ? t.month.slice(0, 4) : t.month
    const c = counts.get(key) || { count: 0, captured: 0 }
    counts.set(key, { count: c.count + t.count, captured: c.captured + t.captured })
  }
  const keys = []
  if (byYear) for (let y = y0; y <= y1; y++) keys.push(String(y))
  else for (let i = 0; i < span; i++) {
    const y = y0 + Math.floor((m0 - 1 + i) / 12), m = (m0 - 1 + i) % 12 + 1
    keys.push(`${y}-${String(m).padStart(2, '0')}`)
  }
  const buckets = keys.map(key => ({ key, ...(counts.get(key) || { count: 0, captured: 0 }) }))
  const max = Math.max(...buckets.map(b => b.count), 1)
  const labelEvery = Math.ceil(buckets.length / 12)
  const fmtKey = k => byYear ? k : new Date(`${k}-01T00:00:00`).toLocaleDateString(undefined, { month: 'short', year: '2-digit' })
  return (
    <div className="cols">
      <div className="cols-plot">
        {buckets.map(b => (
          <div
            key={b.key}
            className="col-hit"
            onMouseMove={e => onTip({ x: e.clientX, y: e.clientY, lines: [fmtKey(b.key), `${b.count.toLocaleString()} items`, `${b.captured.toLocaleString()} with a capture date`] })}
            onMouseLeave={() => onTip(null)}
          >
            {b.count > 0 && <div className="col" style={{ height: `${Math.max(b.count / max * 100, 1.5)}%` }} />}
          </div>
        ))}
      </div>
      <div className="cols-axis">
        {buckets.map((b, i) => <span key={b.key}>{i % labelEvery === 0 ? fmtKey(b.key) : ''}</span>)}
      </div>
    </div>
  )
}

function StorageTable({ groups }) {
  return (
    <table className="files-table viz-table">
      <thead><tr><th>Provenance</th>{KIND_ORDER.map(k => <th key={k} style={{ textAlign: 'right' }}>{KIND_LABEL[k]}</th>)}<th style={{ textAlign: 'right' }}>Total</th></tr></thead>
      <tbody>
        {groups.map(g => (
          <tr key={g.group}>
            <td>{g.group}</td>
            {KIND_ORDER.map(k => <td key={k} style={{ textAlign: 'right' }}>{g.kinds[k] ? `${g.kinds[k].count} · ${fmtBytes(g.kinds[k].bytes)}` : '—'}</td>)}
            <td style={{ textAlign: 'right' }}>{fmtBytes(g.bytes)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export default function LibraryCharts() {
  const [data, setData]   = useState(null)
  const [tip, setTip]     = useState(null)
  const [table, setTable] = useState(false)
  const ref = useRef(null)

  useEffect(() => { api.overview().then(setData).catch(() => {}) }, [])

  if (!data || data.groups.length === 0) return null
  const captured = data.timeline.reduce((a, t) => a + t.captured, 0)
  const total = data.timeline.reduce((a, t) => a + t.count, 0)
  const place = t => t && { ...t, x: t.x + 14, y: t.y + 14 }

  return (
    <div ref={ref}>
      <div className="section-header" style={{ marginTop: 8 }}>
        <div>
          <div className="section-title">Library map</div>
          <div className="section-sub">Where your storage goes, and when things were captured</div>
        </div>
        <button className="btn sm" onClick={() => setTable(t => !t)}>{table ? 'Show charts' : 'Show as table'}</button>
      </div>
      <div className="viz-grid">
        <div className="card">
          <div className="card-title">Storage by provenance</div>
          {table ? <StorageTable groups={data.groups} /> : <StorageBars groups={data.groups} onTip={t => setTip(place(t))} />}
        </div>
        <div className="card">
          <div className="card-title">Timeline</div>
          <div className="cfg-muted" style={{ marginBottom: 8 }}>
            {captured.toLocaleString()} of {total.toLocaleString()} items have a capture date; the rest are placed by when they were added.
          </div>
          {table
            ? <table className="files-table viz-table"><thead><tr><th>Month</th><th style={{ textAlign: 'right' }}>Items</th></tr></thead>
                <tbody>{data.timeline.map(t => <tr key={t.month}><td>{t.month}</td><td style={{ textAlign: 'right' }}>{t.count}</td></tr>)}</tbody></table>
            : <Timeline timeline={data.timeline} onTip={t => setTip(place(t))} />}
        </div>
      </div>
      <Tooltip tip={tip} />
    </div>
  )
}
