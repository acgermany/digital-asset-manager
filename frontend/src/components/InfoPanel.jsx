import { useState, useEffect, useCallback } from 'react'
import { api, fmtBytes } from '../api'

const TIERS = ['critical', 'precious', 'standard', 'low', 'junk']

function when(iso) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

function Row({ label, children, mono }) {
  if (children === null || children === undefined || children === '') return null
  return (
    <div className="info-row">
      <span className="info-label">{label}</span>
      <span className={`info-value${mono ? ' mono' : ''}`}>{children}</span>
    </div>
  )
}

function Section({ title, children, defaultOpen = true, count }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="info-section">
      <button className="info-section-head" onClick={() => setOpen(o => !o)}>
        <span>{open ? '▾' : '▸'} {title}</span>
        {count != null && <span className="chip-count">{count}</span>}
      </button>
      {open && <div className="info-section-body">{children}</div>}
    </div>
  )
}

function Copyable({ text }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1200) } catch { /* no clipboard */ }
  }
  return (
    <span className="copyable">
      <span className="mono break">{text}</span>
      <button className="btn sm" onClick={copy}>{done ? 'Copied' : 'Copy'}</button>
    </span>
  )
}

function KeyValues({ data }) {
  const entries = Object.entries(data || {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && v.length === 0))
  if (!entries.length) return <div className="cfg-muted">None</div>
  return entries.map(([k, v]) => (
    <Row key={k} label={k}>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</Row>
  ))
}

function TagEditor({ tags, onSave, suggestions }) {
  const [draft, setDraft] = useState('')
  const add = (t) => {
    const v = t.trim()
    if (v && !tags.some(x => x.toLowerCase() === v.toLowerCase())) onSave([...tags, v])
    setDraft('')
  }
  return (
    <div className="tag-editor">
      {tags.map(t => (
        <span key={t} className="tag-pill">
          {t}
          <button aria-label={`Remove tag ${t}`} onClick={() => onSave(tags.filter(x => x !== t))}>×</button>
        </span>
      ))}
      <input
        className="search tag-input"
        list="tag-suggestions"
        placeholder="Add tag…"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(draft) }
          if (e.key === 'Backspace' && !draft && tags.length) onSave(tags.slice(0, -1))
        }}
        onBlur={() => draft && add(draft)}
      />
      <datalist id="tag-suggestions">
        {suggestions.map(s => <option key={s.tag} value={s.tag} />)}
      </datalist>
    </div>
  )
}

export default function InfoPanel({ fileId }) {
  const [d, setD]       = useState(null)
  const [err, setErr]   = useState(null)
  const [sugg, setSugg] = useState([])

  const load = useCallback(async () => {
    try {
      const next = await api.fileDetails(fileId)
      setD(prev => (next.file_id === fileId ? next : prev))
      setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [fileId])

  useEffect(() => { setD(null); load() }, [load])
  useEffect(() => { api.tags().then(t => setSugg(t.tags)).catch(() => {}) }, [])

  const update = async (changes) => {
    try {
      const next = await api.updateAsset(d.asset.id, changes)
      setD({ file_id: fileId, ...next })
      if (changes.tags) api.tags().then(t => setSugg(t.tags)).catch(() => {})
    } catch (e) {
      setErr(e.message)
    }
  }

  if (err) return <div className="info-panel"><div className="error-box">{err}</div></div>
  if (!d || d.file_id !== fileId) return <div className="info-panel"><span className="spinner sm" /></div>

  const a = d.asset
  const m = d.metadata
  const thisFile = d.files.find(f => f.id === fileId) || d.files[0]

  return (
    <div className="info-panel">
      <Section title="Overview">
        <div className="info-row">
          <span className="info-label">Tier</span>
          <span className="info-value">
            <select className="search" value={a.value_tier} onChange={e => update({ value_tier: e.target.value })}>
              {TIERS.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <span className="cfg-muted"> {a.tier_source === 'user' ? 'set by you' : `${a.tier_source}${a.tier_confidence != null ? `, ${Math.round(a.tier_confidence * 100)}%` : ''}`}</span>
          </span>
        </div>
        {a.tier_reasons.length > 0 && a.tier_source !== 'user' && (
          <ul className="info-reasons">{a.tier_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
        )}
        <Row label="Protected">{a.protected ? 'Yes — never deleted automatically' : 'No'}</Row>
        <div className="info-row info-row-block">
          <span className="info-label">Tags</span>
          <TagEditor tags={a.tags} suggestions={sugg} onSave={tags => update({ tags })} />
        </div>
      </Section>

      <Section title="File">
        <Row label="Name">{thisFile?.filename}</Row>
        <Row label="Size">{`${fmtBytes(a.filesize)} (${a.filesize.toLocaleString()} bytes)`}</Row>
        <Row label="Type">{a.kind}{a.mime_type ? ` · ${a.mime_type}` : ''}</Row>
        <div className="info-row info-row-block">
          <span className="info-label">SHA-256</span>
          <Copyable text={a.sha256} />
        </div>
        <Row label="Captured">{a.captured_at ? when(a.captured_at) : null}</Row>
        <Row label="First seen">{when(a.first_seen_at)}</Row>
        <Row label="Metadata">{a.metadata_status}</Row>
      </Section>

      <Section title="Ingest history" count={d.ingests.length} defaultOpen={d.ingests.length > 0}>
        {d.ingests.length === 0 && <div className="cfg-muted">Found by a catalog scan, not ingested.</div>}
        {d.ingests.map(i => (
          <div key={i.id} className="info-card">
            <Row label="Ingested">{when(i.ingest_date)}</Row>
            <Row label="As">{i.filename}</Row>
            <Row label="Provenance">{i.provenance}</Row>
            {i.provenance_meta && <Row label="Sidecar" mono>{JSON.stringify(i.provenance_meta)}</Row>}
            <Row label="Outcome">{`${i.status}${i.decision ? ` (${i.decision})` : ''}`}</Row>
            <Row label="From" mono>{i.original_path}</Row>
          </div>
        ))}
      </Section>

      <Section title="Copies on disk" count={d.files.length}>
        {d.files.map(f => (
          <div key={f.id} className="info-card">
            <Row label="Path" mono>{f.path}</Row>
            <Row label="Source">{`${f.source}${f.provenance ? ` · ${f.provenance}` : ''}`}</Row>
            <Row label="Recorded">{when(f.created_at)}</Row>
            <Row label="On disk">{f.exists ? 'Yes' : 'Missing (drive not mounted?)'}</Row>
            {f.marked_for_deletion && <Row label="Marked">for deletion</Row>}
          </div>
        ))}
      </Section>

      {Object.keys(m.media_summary).length > 0 && (
        <Section title="Media"><KeyValues data={m.media_summary} /></Section>
      )}
      {Object.keys(m.exif).length > 0 && (
        <Section title="EXIF / camera" count={Object.keys(m.exif).length}><KeyValues data={m.exif} /></Section>
      )}
      {Object.keys(m.document).length > 0 && (
        <Section title="Document"><KeyValues data={m.document} /></Section>
      )}

      <Section title="Search & AI" defaultOpen={false}>
        <Row label="Embeddings">{d.embeddings.length ? d.embeddings.map(e => `${e.model} (${when(e.created_at)})`).join(', ') : 'Not embedded yet'}</Row>
        <Row label="Segments">{String(d.segments)}</Row>
        <div className="info-row info-row-block">
          <span className="info-label">Indexed text</span>
          <span className="info-value mono break">{a.search_text || '—'}</span>
        </div>
      </Section>

      {d.reviews.length > 0 && (
        <Section title="Reviews" count={d.reviews.length} defaultOpen={false}>
          {d.reviews.map(r => (
            <Row key={r.id} label={r.kind.replace(/_/g, ' ')}>{`${r.status}${r.resolution ? ` · ${r.resolution}` : ''}`}</Row>
          ))}
        </Section>
      )}

      <Section title="History" count={d.events.length} defaultOpen={false}>
        {d.events.map((e, i) => (
          <div key={i} className="info-event">
            <span className="cfg-muted">{when(e.ts)}</span> <strong>{e.action.replace(/_/g, ' ')}</strong>
            <span className="cfg-muted"> · {e.actor}</span>
          </div>
        ))}
      </Section>

      {m.mediainfo && (
        <Section title="Raw mediainfo JSON" defaultOpen={false}>
          <pre className="info-json">{JSON.stringify(m.mediainfo, null, 2)}</pre>
        </Section>
      )}
    </div>
  )
}
