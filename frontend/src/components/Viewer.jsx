import { useState, useEffect, useRef, useCallback } from 'react'
import { api, fmtBytes } from '../api'
import SegmentPanel from './SegmentPanel'

function fmtDuration(s) {
  if (!s) return null
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

function ImageView({ url, alt }) {
  const [fit, setFit]       = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [err, setErr]       = useState(false)
  useEffect(() => { setFit(true); setLoaded(false); setErr(false) }, [url])
  if (err) return <div className="viewer-msg">This image could not be displayed.</div>
  return (
    <div className={`viewer-image${fit ? ' fit' : ''}`} onClick={() => setFit(f => !f)} title={fit ? 'Click for actual size' : 'Click to fit'}>
      {!loaded && <div className="viewer-msg"><span className="spinner" /> Loading image…</div>}
      <img src={url} alt={alt} onLoad={() => setLoaded(true)} onError={() => setErr(true)}
           style={loaded ? undefined : { display: 'none' }} />
    </div>
  )
}

function ProxyWait({ info, onRetry }) {
  const p = info.proxy
  if (p.status === 'failed') {
    return (
      <div className="viewer-msg">
        <div>Couldn’t create a playable preview.</div>
        <div className="cfg-muted">{p.error}</div>
        <button className="btn sm" onClick={onRetry}>Try again</button>
      </div>
    )
  }
  const pct = Math.round((p.progress || 0) * 100)
  return (
    <div className="viewer-msg">
      <div><span className="spinner" /> {p.status === 'queued' ? 'Waiting to create a preview…' : 'Creating a browser-friendly preview…'}</div>
      <div className="cfg-muted">
        This {info.video_codec ? info.video_codec.toUpperCase() + ' ' : ''}video can’t play in the browser directly,
        so ffmpeg is making a {info.height && info.height < 720 ? '' : '720p '}H.264 copy. It’s kept for next time.
      </div>
      <div className="progress-track" style={{ width: 320 }}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="cfg-muted">{pct}%</div>
    </div>
  )
}

function TextView({ url }) {
  const [text, setText] = useState(null)
  useEffect(() => {
    fetch(url).then(r => r.text()).then(t => setText(t.slice(0, 200_000))).catch(() => setText('Could not load file.'))
  }, [url])
  return <pre className="viewer-text">{text ?? 'Loading…'}</pre>
}

export default function Viewer({ files, index, onIndex, onClose }) {
  const file = files[index]
  const [loaded, setInfo] = useState(null)
  const [err, setErr]     = useState(null)
  const mediaRef = useRef(null)
  const current = useRef(null)
  current.current = file?.id

  const load = useCallback(async () => {
    if (!file) return
    try {
      const next = await api.viewInfo(file.id)
      if (current.current === next.file_id) { setInfo(next); setErr(null) }   // drop stale responses
    } catch (e) {
      if (current.current === file.id) setErr(e.message)
    }
  }, [file])

  // Never render info that belongs to a different file than the header shows.
  const info = loaded && loaded.file_id === file?.id ? loaded : null

  useEffect(() => { setErr(null); load() }, [load])

  // Poll while a proxy is being made
  const waiting = info?.proxy && ['queued', 'processing'].includes(info.proxy.status)
  useEffect(() => {
    if (!waiting) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [waiting, load])

  useEffect(() => {
    const onKey = e => {
      if (e.target.closest?.('input, textarea, select')) return
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight' && index < files.length - 1) onIndex(index + 1)
      if (e.key === 'ArrowLeft' && index > 0) onIndex(index - 1)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [index, files.length, onIndex, onClose])

  if (!file) return null
  const type = info?.type
  const playable = type === 'video' && (!info.proxy || info.proxy.status === 'ready')
  const withSegments = (type === 'video' && playable) || type === 'audio'

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal viewer" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <button className="btn sm" disabled={index === 0} onClick={() => onIndex(index - 1)} title="Previous (←)">‹</button>
          <button className="btn sm" disabled={index >= files.length - 1} onClick={() => onIndex(index + 1)} title="Next (→)">›</button>
          <span className="modal-title" title={file.filepath}>{file.filename}</span>
          <span className="viewer-meta">
            {index + 1} / {files.length} · {fmtBytes(file.filesize)}
            {info?.duration ? ` · ${fmtDuration(info.duration)}` : ''}
            {info?.width ? ` · ${info.width}×${info.height}` : ''}
          </span>
          <a href={`/api/files/${file.id}/content`} download={file.filename} className="btn sm">Download</a>
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>

        <div className={`viewer-body${withSegments ? ' with-segments' : ''}`}>
          <div className="viewer-stage">
            {err && <div className="viewer-msg" style={{ color: 'var(--red)' }}>{err}</div>}
            {!info && !err && <div className="viewer-msg"><span className="spinner" /></div>}
            {type === 'image' && <ImageView url={info.url} alt={file.filename} />}
            {type === 'video' && !playable && <ProxyWait info={info} onRetry={async () => { await api.retryProxy(file.id); load() }} />}
            {type === 'video' && playable && (
              <video ref={mediaRef} key={info.url} src={info.url} controls autoPlay className="viewer-video" />
            )}
            {type === 'audio' && (
              <div className="viewer-msg">
                <audio ref={mediaRef} key={info.url} src={info.url} controls autoPlay style={{ width: 'min(560px, 100%)' }} />
              </div>
            )}
            {type === 'pdf' && <iframe src={info.url} title={file.filename} className="viewer-pdf" />}
            {type === 'text' && <TextView url={info.url} />}
            {type === 'other' && (
              <div className="viewer-msg">
                <div>No preview for this file type.</div>
                <a href={`/api/files/${file.id}/content`} download={file.filename} className="btn primary">Download</a>
              </div>
            )}
          </div>
          {withSegments && (
            <div className="viewer-side">
              <SegmentPanel fileId={file.id} mediaRef={mediaRef} />
            </div>
          )}
        </div>
        <div className="viewer-path">{file.filepath}</div>
      </div>
    </div>
  )
}
