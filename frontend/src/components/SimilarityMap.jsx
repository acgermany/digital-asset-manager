import { useState, useEffect, useRef, useMemo } from 'react'
import { api, fmtBytes } from '../api'

// Emphasis encoding: the two protected tiers get color, everything else recedes to gray.
const GROUPS = [
  { key: 'precious', label: 'Precious', color: 'var(--series-1)' },
  { key: 'critical', label: 'Critical', color: 'var(--series-2)' },
  { key: 'rest',     label: 'Everything else', color: 'var(--deemph)' },
]
const groupOf = tier => (tier === 'precious' || tier === 'critical' ? tier : 'rest')
const PAD = 24
const R = 5          // 10px markers
const HIT = 14       // hover radius, bigger than the mark

export default function SimilarityMap({ kind, onOpen }) {
  const [data, setData]   = useState(null)
  const [err, setErr]     = useState(null)
  const [size, setSize]   = useState({ w: 800, h: 520 })
  const [hover, setHover] = useState(null)
  const [view, setView]   = useState({ k: 1, x: 0, y: 0 })   // zoom + pan
  const [hidden, setHidden] = useState({})
  const wrap = useRef(null)
  const drag = useRef(null)

  useEffect(() => {
    let alive = true
    api.libraryMap().then(d => alive && setData(d)).catch(e => alive && setErr(e.message))
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!wrap.current) return
    const ro = new ResizeObserver(([e]) => {
      const w = Math.max(320, e.contentRect.width)
      setSize({ w, h: Math.max(360, Math.min(640, w * 0.62)) })
    })
    ro.observe(wrap.current)
    return () => ro.disconnect()
  }, [data])

  const points = useMemo(() => (data?.points || []).filter(p => (!kind || p.kind === kind) && !hidden[groupOf(p.tier)]), [data, kind, hidden])
  // Draw gray first so the colored (important) points sit on top.
  const ordered = useMemo(() => [...points].sort((a, b) => (groupOf(a.tier) === 'rest' ? 0 : 1) - (groupOf(b.tier) === 'rest' ? 0 : 1)), [points])

  const sx = x => PAD + (x * (size.w - 2 * PAD)) * view.k + view.x
  const sy = y => PAD + (y * (size.h - 2 * PAD)) * view.k + view.y

  const nearest = (mx, my) => {
    let best = null, bd = HIT * HIT
    for (const p of points) {
      const dx = sx(p.x) - mx, dy = sy(p.y) - my, d = dx * dx + dy * dy
      if (d < bd) { bd = d; best = p }
    }
    return best
  }

  const local = e => {
    const r = wrap.current.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top]
  }

  const onMove = e => {
    const [mx, my] = local(e)
    if (drag.current) {
      setView(v => ({ ...v, x: drag.current.vx + mx - drag.current.mx, y: drag.current.vy + my - drag.current.my }))
      drag.current.moved = true
      return
    }
    setHover(nearest(mx, my))
  }

  const onWheel = e => {
    e.preventDefault()
    const [mx, my] = local(e)
    setView(v => {
      const k = Math.min(20, Math.max(1, v.k * (e.deltaY < 0 ? 1.2 : 1 / 1.2)))
      const f = k / v.k
      return { k, x: mx - (mx - v.x) * f, y: my - (my - v.y) * f }
    })
  }

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  })

  if (err) return <div className="error-box">{err}</div>
  if (!data) return <div className="card cfg-empty"><span className="spinner sm" /> Laying out your library…</div>
  if (!data.points.length) {
    return (
      <div className="card cfg-empty" style={{ textAlign: 'center', padding: 40 }}>
        <div style={{ marginBottom: 12 }}>The map places similar items close together, using the embeddings from semantic search.</div>
        <div>Nothing is embedded yet. Turn on <strong>Semantic search</strong> above, or run a batch in Workflows → Embeddings.</div>
      </div>
    )
  }

  const counts = Object.fromEntries(GROUPS.map(g => [g.key, (data.points || []).filter(p => (!kind || p.kind === kind) && groupOf(p.tier) === g.key).length]))
  const openPoint = p => onOpen(ordered, ordered.indexOf(p))

  return (
    <div className="card simmap-card">
      <div className="simmap-head">
        <div className="viz-legend" style={{ margin: 0 }}>
          {GROUPS.map(g => (
            <button key={g.key} className={`viz-legend-item toggle${hidden[g.key] ? ' off' : ''}`} onClick={() => setHidden(h => ({ ...h, [g.key]: !h[g.key] }))}>
              <span className="viz-swatch round" style={{ background: g.color }} />{g.label} <span className="chip-count">{counts[g.key]}</span>
            </button>
          ))}
        </div>
        <span className="cfg-muted">
          {data.shown < data.total ? `${data.shown.toLocaleString()} of ${data.total.toLocaleString()} shown · ` : ''}
          Similar items sit together · scroll to zoom, drag to pan
        </span>
        {(view.k !== 1 || view.x || view.y) ? <button className="btn sm" onClick={() => setView({ k: 1, x: 0, y: 0 })}>Reset view</button> : null}
      </div>
      <div
        ref={wrap}
        className="simmap"
        style={{ height: size.h }}
        onMouseMove={onMove}
        onMouseLeave={() => { setHover(null); drag.current = null }}
        onMouseDown={e => { const [mx, my] = local(e); drag.current = { mx, my, vx: view.x, vy: view.y, moved: false } }}
        onMouseUp={e => {
          const wasDrag = drag.current?.moved
          drag.current = null
          if (!wasDrag) { const [mx, my] = local(e); const p = nearest(mx, my); if (p) openPoint(p) }
        }}
        role="img"
        aria-label={`Similarity map of ${points.length} items`}
      >
        <svg width={size.w} height={size.h}>
          {ordered.map(p => {
            const g = GROUPS.find(x => x.key === groupOf(p.tier))
            const cx = sx(p.x), cy = sy(p.y)
            if (cx < -R || cy < -R || cx > size.w + R || cy > size.h + R) return null
            return <circle key={p.asset_id} cx={cx} cy={cy} r={hover === p ? R + 2 : R} fill={g.color} stroke="var(--surface)" strokeWidth="2" />
          })}
        </svg>
        {hover && (
          <div className="simmap-tip" style={{ left: Math.min(sx(hover.x) + 14, size.w - 230), top: Math.min(sy(hover.y) + 14, size.h - 220) }}>
            {['image', 'video'].includes(hover.kind) && <img src={`/api/files/${hover.file_id}/thumbnail`} alt="" />}
            <div className="viz-tooltip-title">{hover.filename}</div>
            <div className="cfg-muted">{hover.kind} · {fmtBytes(hover.filesize)}{groupOf(hover.tier) !== 'rest' ? ` · ${hover.tier}` : ''}</div>
          </div>
        )}
      </div>
    </div>
  )
}
