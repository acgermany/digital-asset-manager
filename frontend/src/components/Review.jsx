import { useState, useEffect, useCallback } from 'react'
import { api, fmtBytes } from '../api'
import ReviewThumb from './ReviewThumb'
import Viewer from './Viewer'

const TIERS = ['critical', 'precious', 'standard', 'low', 'junk']
const KINDS = [['', 'All'], ['exact_name_differs', 'Same content, different name'], ['classification', 'Classification']]

function DuplicateReview({ r, onResolve, onOpen }) {
  // Identical content, so the library copy's preview is also the incoming file's preview.
  const first = (r.details.existing || [])[0]
  const kind = r.asset?.kind
  return (
    <>
      <div className="review-title">Same content, different name</div>
      <div className="review-body">
      {first && (
        <ReviewThumb file={{ id: first.file_id, filename: first.filename }} kind={kind}
                     caption="Identical content" onOpen={() => onOpen({ id: first.file_id, filename: first.filename, kind })} />
      )}
      <div className="review-compare">
        <div>
          <div className="review-label">Incoming (held in hot folder/_review)</div>
          <div className="review-name">{r.details.incoming_name}</div>
          <div className="cell-sub">{r.ingest?.provenance} · {fmtBytes(r.ingest?.filesize)}</div>
        </div>
        <div>
          <div className="review-label">Already in the library as</div>
          {(r.details.existing || []).map(e => (
            <div key={e.file_id}>
              <div className="review-name">{e.filename}</div>
              <div className="cell-sub">{e.path}</div>
            </div>
          ))}
        </div>
      </div>
      </div>
      <div className="review-actions">
        <button className="btn sm danger" onClick={() => onResolve('delete_incoming')}>Delete incoming copy</button>
        <button className="btn sm" onClick={() => onResolve('keep_both')}>Keep both names</button>
      </div>
    </>
  )
}

function ClassificationReview({ r, onResolve, onOpen }) {
  const [tier, setTier] = useState(r.asset?.value_tier || 'standard')
  const file = r.asset?.files?.[0]
  return (
    <>
      <div className="review-title">
        Is this <span className={`tag tier-${r.asset?.value_tier}`}>{r.asset?.value_tier}</span>?
        <span className="cfg-muted"> confidence {Math.round((r.asset?.tier_confidence || 0) * 100)}%</span>
      </div>
      <div className="review-body">
        {file && (
          <ReviewThumb file={file} kind={r.asset?.kind}
                       onOpen={() => onOpen({ id: file.id, filename: file.filename, kind: r.asset?.kind, value_tier: r.asset?.value_tier })} />
        )}
        <div style={{ minWidth: 0 }}>
          {file && (
            <>
              <div className="review-name">{file.filename}</div>
              <div className="cell-sub">{file.path}</div>
            </>
          )}
          <ul className="review-reasons">
            {(r.asset?.tier_reasons || []).map((reason, i) => <li key={i}>{reason}</li>)}
          </ul>
        </div>
      </div>
      <div className="review-actions">
        <button className="btn sm primary" onClick={() => onResolve('confirm')}>Yes, confirm</button>
        <span className="cfg-muted">or set to</span>
        <select className="search" value={tier} onChange={e => setTier(e.target.value)}>
          {TIERS.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
        <button className="btn sm" onClick={() => onResolve('set_tier', tier)}>Apply</button>
      </div>
    </>
  )
}

export default function Review() {
  const [kind, setKind] = useState('')
  const [data, setData] = useState(null)
  const [err, setErr]   = useState(null)
  const [viewing, setViewing] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await api.reviews(kind ? { kind } : {}))
      setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [kind])

  useEffect(() => { load() }, [load])

  const resolve = async (id, resolution, tier) => {
    try {
      await api.resolveReview(id, resolution, tier)
      load()
    } catch (e) {
      setErr(e.message)
    }
  }

  const counts = data?.counts || {}

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Review</div>
          <div className="section-sub">Decisions the app won’t make on its own. Most important first.</div>
        </div>
      </div>
      {err && <div className="error-box">{err}</div>}

      <div className="chip-row" style={{ marginBottom: 12 }}>
        {KINDS.map(([value, label]) => (
          <button key={label} className={`chip${kind === value ? ' active' : ''}`} onClick={() => setKind(value)}>
            {label} <span className="chip-count">{value ? counts[value] || 0 : Object.values(counts).reduce((a, b) => a + b, 0)}</span>
          </button>
        ))}
      </div>

      {data?.reviews.length === 0 && <div className="card cfg-empty">Nothing to review.</div>}
      {data?.reviews.map(r => (
        <div key={r.id} className="card review-card">
          {r.kind === 'exact_name_differs'
            ? <DuplicateReview r={r} onResolve={(res) => resolve(r.id, res)} onOpen={setViewing} />
            : <ClassificationReview r={r} onResolve={(res, tier) => resolve(r.id, res, tier)} onOpen={setViewing} />}
        </div>
      ))}
      {viewing && <Viewer files={[viewing]} index={0} onIndex={() => {}} onClose={() => setViewing(null)} />}
    </>
  )
}
