import { useState, useEffect, useRef } from 'react'
import SegmentPanel from './SegmentPanel'

function AudioPlayer({ url, filepath, mediaRef }) {
  const [errMsg, setErrMsg] = useState(null)
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-muted)', marginBottom: 14, wordBreak: 'break-all' }}>
        {filepath}
      </div>
      {errMsg
        ? <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12 }}>
            {errMsg}
          </div>
        : <audio
            ref={mediaRef}
            key={url}
            src={url}
            controls
            autoPlay
            style={{ width: '100%', maxWidth: 560 }}
            onError={e => {
              const code = e.target.error?.code
              const msgs = {
                1: 'Playback aborted.',
                2: 'Network error — check that the file is still accessible.',
                3: 'Could not decode — the file may be corrupt or the transcode failed.',
                4: 'Could not play this file.',
              }
              setErrMsg(msgs[code] || 'Could not play this file.')
            }}
          />
      }
      <a href={url} download style={{ fontSize: 12, color: 'var(--accent-lt)', marginTop: 12, display: 'inline-block' }}>
        Download file
      </a>
    </div>
  )
}

function mediaType(filename) {
  const ext = (filename.split('.').pop() || '').toLowerCase()
  if (['mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v', 'wmv', 'flv', 'mpg', 'mpeg', 'ts'].includes(ext)) return 'video'
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'tiff', 'tif', 'ico', 'avif'].includes(ext)) return 'image'
  if (['mp3', 'm4a', 'wav', 'flac', 'aac', 'ogg', 'wma', 'opus', 'aiff'].includes(ext)) return 'audio'
  if (ext === 'pdf') return 'pdf'
  if (['txt', 'md', 'csv', 'json', 'xml', 'log', 'yml', 'yaml', 'ini', 'conf', 'sh', 'py', 'js'].includes(ext)) return 'text'
  return 'other'
}

function TextPreview({ url }) {
  const [text, setText] = useState(null)
  const [err, setErr]   = useState(null)

  useEffect(() => {
    fetch(url)
      .then(r => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.text() })
      .then(t => setText(t.slice(0, 100_000)))
      .catch(e => setErr(e.message))
  }, [url])

  if (err)   return <div style={{ padding: 24, color: 'var(--red)' }}>{err}</div>
  if (!text) return <div style={{ padding: 24, color: 'var(--text-muted)' }}>Loading…</div>
  return (
    <pre style={{
      padding: 20, margin: 0, overflowY: 'auto', maxHeight: 'calc(90vh - 56px)',
      fontSize: 12, lineHeight: 1.6, color: 'var(--text)',
      whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--bg)',
    }}>
      {text}
    </pre>
  )
}

export default function FilePreview({ file, onClose }) {
  const mediaRef = useRef(null)

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!file) return null

  const url  = `/api/files/${file.id}/content`
  const type = mediaType(file.filename)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>

        <div className="modal-header">
          <span className="modal-title" title={file.filepath}>{file.filename}</span>
          <a
            href={url}
            download={file.filename}
            className="btn sm"
            style={{ marginRight: 8, flexShrink: 0 }}
            onClick={e => e.stopPropagation()}
          >
            Download
          </a>
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>

        {/* ── Video: player left, segment panel right ── */}
        {type === 'video' && (
          <div className="modal-body modal-body-media-h">
            <div className="media-player-pane">
              <video
                ref={mediaRef}
                key={url}
                src={url}
                controls
                autoPlay
                style={{ width: '100%', maxHeight: '100%', display: 'block' }}
              />
            </div>
            <div className="media-seg-pane">
              <SegmentPanel fileId={file.id} mediaRef={mediaRef} />
            </div>
          </div>
        )}

        {/* ── Audio: player top, segment panel below ── */}
        {type === 'audio' && (
          <div className="modal-body modal-body-media-v">
            <div className="audio-player-pane">
              <AudioPlayer url={url} filepath={file.filepath} mediaRef={mediaRef} />
            </div>
            <div className="audio-seg-pane">
              <SegmentPanel fileId={file.id} mediaRef={mediaRef} />
            </div>
          </div>
        )}

        {/* ── Image ── */}
        {type === 'image' && (
          <div className="modal-body">
            <img
              src={url}
              alt={file.filename}
              style={{ maxWidth: '100%', maxHeight: 'calc(90vh - 56px)', objectFit: 'contain', display: 'block', margin: '0 auto' }}
            />
          </div>
        )}

        {/* ── PDF ── */}
        {type === 'pdf' && (
          <div className="modal-body" style={{ display: 'block', padding: 0 }}>
            <iframe
              src={url}
              title={file.filename}
              style={{ width: '100%', height: 'calc(90vh - 56px)', border: 'none', display: 'block' }}
            />
          </div>
        )}

        {/* ── Text ── */}
        {type === 'text' && (
          <div className="modal-body modal-body-light">
            <TextPreview url={url} />
          </div>
        )}

        {/* ── Unsupported ── */}
        {type === 'other' && (
          <div className="modal-body modal-body-light" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
              <div style={{ marginBottom: 8, fontSize: 13 }}>No preview available for this file type.</div>
              <div style={{ fontSize: 12, marginBottom: 24, fontFamily: 'monospace' }}>{file.filename}</div>
              <a href={url} download={file.filename} className="btn primary">Download</a>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
