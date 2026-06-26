import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

// Format seconds → m:ss.mmm  (or h:mm:ss.mmm for long files)
function fmtTime(s) {
  if (s == null || isNaN(s)) return '0:00.000'
  const h   = Math.floor(s / 3600)
  const m   = Math.floor((s % 3600) / 60)
  const sec = (s % 60).toFixed(3).padStart(6, '0')
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${sec}`
  return `${m}:${sec}`
}

function metaSummary(raw) {
  try {
    const obj  = JSON.parse(raw || '{}')
    const keys = Object.keys(obj)
    if (keys.length === 0) return null
    return keys.length === 1 ? keys[0] : `${keys.length} keys: ${keys.slice(0, 3).join(', ')}${keys.length > 3 ? '…' : ''}`
  } catch {
    return null
  }
}

function prettyMeta(raw) {
  try   { return JSON.stringify(JSON.parse(raw || '{}'), null, 2) }
  catch { return raw }
}

const EMPTY_FORM = { in_point: 0, out_point: 0, label: '', metadata: '{}' }

export default function SegmentPanel({ fileId, mediaRef }) {
  const [segments,  setSegments]  = useState([])
  const [editId,    setEditId]    = useState(null)   // null | 'new' | <number>
  const [form,      setForm]      = useState(EMPTY_FORM)
  const [metaErr,   setMetaErr]   = useState(null)
  const [expanded,  setExpanded]  = useState({})
  const [err,       setErr]       = useState(null)

  const load = useCallback(async () => {
    try {
      const d = await api.segments(fileId)
      setSegments(d.segments)
    } catch (e) {
      setErr(e.message)
    }
  }, [fileId])

  useEffect(() => { load() }, [load])

  const getTime  = () => mediaRef.current?.currentTime ?? 0
  const seekTo   = (t) => { if (mediaRef.current) mediaRef.current.currentTime = t }
  const setField = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const validateMeta = (s) => {
    try { JSON.parse(s); setMetaErr(null); return true }
    catch (e) { setMetaErr(e.message); return false }
  }

  const onMetaChange = (s) => { setField('metadata', s); validateMeta(s) }

  const openNew = () => {
    const t = getTime()
    setForm({ in_point: t, out_point: t, label: '', metadata: '{}' })
    setMetaErr(null)
    setEditId('new')
  }

  const openEdit = (seg) => {
    setForm({
      in_point:  seg.in_point,
      out_point: seg.out_point,
      label:     seg.label    || '',
      metadata:  seg.metadata || '{}',
    })
    setMetaErr(null)
    setEditId(seg.id)
  }

  const cancel = () => setEditId(null)

  const save = async () => {
    if (!validateMeta(form.metadata)) return
    try {
      if (editId === 'new') {
        await api.createSegment(fileId, form)
      } else {
        await api.updateSegment(editId, form)
      }
      await load()
      setEditId(null)
    } catch (e) {
      setErr(e.message)
    }
  }

  const remove = async (id) => {
    try {
      await api.deleteSegment(id)
      setSegments(s => s.filter(x => x.id !== id))
      if (editId === id) setEditId(null)
    } catch (e) {
      setErr(e.message)
    }
  }

  const toggleExpand = (id) => setExpanded(e => ({ ...e, [id]: !e[id] }))

  return (
    <div className="seg-panel">

      {/* Panel header */}
      <div className="seg-panel-header">
        <span className="seg-panel-title">Segments{segments.length > 0 ? ` (${segments.length})` : ''}</span>
        {editId === null && (
          <button className="btn sm primary" onClick={openNew}>+ New</button>
        )}
      </div>

      {err && <div className="seg-err-banner">{err}</div>}

      {/* Create / Edit form */}
      {editId !== null && (
        <div className="seg-form">
          <div className="seg-form-title">{editId === 'new' ? 'New Segment' : 'Edit Segment'}</div>

          {/* In / Out points */}
          <div className="seg-points">
            {[['in_point', 'In point'], ['out_point', 'Out point']].map(([key, label]) => (
              <div key={key} className="seg-point-field">
                <label className="seg-label">{label}</label>
                <div className="seg-point-row">
                  <input
                    className="seg-num-input"
                    type="number"
                    step="0.001"
                    min="0"
                    value={form[key]}
                    onChange={e => setField(key, parseFloat(e.target.value) || 0)}
                  />
                  <button
                    className="btn sm"
                    onClick={() => setField(key, getTime())}
                    title="Capture current playhead position"
                  >
                    ← now
                  </button>
                </div>
                <div className="seg-time-fmt">{fmtTime(form[key])}</div>
              </div>
            ))}
          </div>

          {/* Label */}
          <div className="seg-field">
            <label className="seg-label">Label</label>
            <input
              className="seg-text-input"
              type="text"
              placeholder="e.g. chorus, action shot, interview…"
              value={form.label}
              onChange={e => setField('label', e.target.value)}
            />
          </div>

          {/* Metadata JSON */}
          <div className="seg-field">
            <label className="seg-label">Metadata <span className="seg-label-hint">(JSON)</span></label>
            <textarea
              className={`seg-textarea${metaErr ? ' seg-textarea-invalid' : ''}`}
              value={form.metadata}
              onChange={e => onMetaChange(e.target.value)}
              rows={7}
              spellCheck={false}
            />
            {metaErr && <div className="seg-meta-err">{metaErr}</div>}
          </div>

          <div className="seg-form-actions">
            <button className="btn sm primary" onClick={save} disabled={!!metaErr}>
              Save
            </button>
            <button className="btn sm" onClick={cancel}>Cancel</button>
          </div>
        </div>
      )}

      {/* Segment list */}
      <div className="seg-list">
        {segments.length === 0 && editId === null && (
          <div className="seg-empty">
            No segments yet — scrub to a position and click + New.
          </div>
        )}

        {segments.map(seg => (
          <div key={seg.id} className={`seg-row${editId === seg.id ? ' seg-row-active' : ''}`}>

            {/* Time range + seek */}
            <div className="seg-row-top">
              <button
                className="btn sm seg-seek-btn"
                onClick={() => seekTo(seg.in_point)}
                title={`Seek to ${fmtTime(seg.in_point)}`}
              >
                ▶
              </button>
              <span className="seg-range">
                {fmtTime(seg.in_point)}<span className="seg-arrow"> → </span>{fmtTime(seg.out_point)}
              </span>
              <div className="seg-row-actions">
                <button className="btn sm" onClick={() => openEdit(seg)}>Edit</button>
                <button className="btn sm danger" onClick={() => remove(seg.id)}>Del</button>
              </div>
            </div>

            {/* Label */}
            {seg.label && <div className="seg-row-label">{seg.label}</div>}

            {/* Metadata summary / expanded */}
            {metaSummary(seg.metadata) && (
              <div className="seg-meta-toggle" onClick={() => toggleExpand(seg.id)}>
                <span className="seg-meta-caret">{expanded[seg.id] ? '▾' : '▸'}</span>
                {expanded[seg.id] ? 'metadata' : metaSummary(seg.metadata)}
              </div>
            )}
            {expanded[seg.id] && (
              <pre className="seg-meta-json">{prettyMeta(seg.metadata)}</pre>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
