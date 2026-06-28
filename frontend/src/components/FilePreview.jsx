import { useState, useEffect, useRef } from 'react'
import { api } from '../api'
import SegmentPanel from './SegmentPanel'

function AudioPlayer({ url, filepath, mediaRef }) {
  const [errMsg, setErrMsg] = useState(null)
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-muted)', marginBottom: 14, wordBreak: 'break-all' }}>
        {filepath}
      </div>
      {errMsg
        ? <div style={{ color: 'var(--red)', fontSize: 13, marginBottom: 12 }}>{errMsg}</div>
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
  if (['mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v', 'wmv', 'flv', 'mpg', 'mpeg', 'ts', 'mxf'].includes(ext)) return 'video'
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
      padding: 20, margin: 0, overflowY: 'auto', maxHeight: 'calc(90vh - 112px)',
      fontSize: 12, lineHeight: 1.6, color: 'var(--text)',
      whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'var(--bg)',
    }}>
      {text}
    </pre>
  )
}

function fmt(bytes) {
  if (!bytes) return '—'
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + ' GB'
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(2) + ' MB'
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(1) + ' KB'
  return bytes + ' B'
}

function fmtDuration(s) {
  if (!s) return '—'
  const h   = Math.floor(s / 3600)
  const m   = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`
  return `${m}:${String(sec).padStart(2,'0')}`
}

function fmtFps(fps) {
  if (!fps) return '—'
  return Number(fps).toFixed(3).replace(/\.?0+$/, '') + ' fps'
}

function MediaInfoPanel({ fileId }) {
  const [info,    setInfo]    = useState(undefined) // undefined = loading, null = not found
  const [queuing, setQueuing] = useState(false)
  const [err,     setErr]     = useState(null)

  const load = async () => {
    try {
      const d = await api.mediainfo(fileId)
      setInfo(d)
    } catch (e) {
      setErr(e.message)
    }
  }

  useEffect(() => { load() }, [fileId])

  const handleScan = async () => {
    setQueuing(true)
    try {
      await api.queueMediainfo(fileId)
      // Poll until results appear
      const poll = setInterval(async () => {
        const d = await api.mediainfo(fileId)
        if (d) { setInfo(d); clearInterval(poll); setQueuing(false) }
      }, 2000)
    } catch (e) {
      setErr(e.message)
      setQueuing(false)
    }
  }

  if (err) return <div style={{ padding: 16, color: 'var(--red)', fontSize: 13 }}>{err}</div>
  if (info === undefined) return <div style={{ padding: 16, color: 'var(--text-muted)', fontSize: 13 }}>Loading…</div>

  if (!info) return (
    <div style={{ padding: 16, textAlign: 'center' }}>
      <div style={{ color: 'var(--text-muted)', fontSize: 13, marginBottom: 12 }}>No technical metadata yet.</div>
      <button className="btn sm primary" onClick={handleScan} disabled={queuing}>
        {queuing ? 'Scanning…' : 'Extract MediaInfo'}
      </button>
    </div>
  )

  const rows = [
    ['Format',       info.format],
    ['Duration',     fmtDuration(info.duration)],
    ['Bitrate',      info.bit_rate ? (info.bit_rate / 1000).toFixed(0) + ' kbps' : '—'],
    ['Video codec',  info.video_codec],
    ['Resolution',   info.width && info.height ? `${info.width} × ${info.height}` : '—'],
    ['Frame rate',   fmtFps(info.frame_rate)],
    ['Timecode',     info.timecode],
    ['Audio codec',  info.audio_codec],
    ['Channels',     info.audio_channels],
    ['Sample rate',  info.audio_sample_rate ? info.audio_sample_rate.toLocaleString() + ' Hz' : '—'],
  ].filter(([, v]) => v != null && v !== '—' && v !== '')

  return (
    <div style={{ overflowY: 'auto', maxHeight: 'calc(90vh - 112px)' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <tbody>
          {rows.map(([label, value]) => (
            <tr key={label} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '7px 12px', color: 'var(--text-muted)', width: '40%', whiteSpace: 'nowrap' }}>{label}</td>
              <td style={{ padding: '7px 12px', fontFamily: 'monospace', fontWeight: 500 }}>{value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--text-muted)' }}>
        Scanned {new Date(info.scanned_at).toLocaleString()}
      </div>
    </div>
  )
}

export default function FilePreview({ file, onClose }) {
  const mediaRef = useRef(null)
  const [tab, setTab] = useState('preview')

  const type        = file ? mediaType(file.filename) : null
  const hasMediaInfo = type === 'video' || type === 'audio'

  useEffect(() => {
    setTab('preview')
  }, [file?.id])

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!file) return null

  const url = `/api/files/${file.id}/content`

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

        {/* Tab bar — only shown for media types */}
        {hasMediaInfo && (
          <div className="modal-tabs">
            {['preview', 'technical'].map(t => (
              <button
                key={t}
                className={`modal-tab${tab === t ? ' active' : ''}`}
                onClick={() => setTab(t)}
              >
                {t === 'preview' ? 'Preview' : 'Technical'}
              </button>
            ))}
          </div>
        )}

        {/* ── Preview tab ── */}
        {tab === 'preview' && (
          <>
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

            {type === 'image' && (
              <div className="modal-body">
                <img
                  src={url}
                  alt={file.filename}
                  style={{ maxWidth: '100%', maxHeight: 'calc(90vh - 56px)', objectFit: 'contain', display: 'block', margin: '0 auto' }}
                />
              </div>
            )}

            {type === 'pdf' && (
              <div className="modal-body" style={{ display: 'block', padding: 0 }}>
                <iframe
                  src={url}
                  title={file.filename}
                  style={{ width: '100%', height: 'calc(90vh - 56px)', border: 'none', display: 'block' }}
                />
              </div>
            )}

            {type === 'text' && (
              <div className="modal-body modal-body-light">
                <TextPreview url={url} />
              </div>
            )}

            {type === 'other' && (
              <div className="modal-body modal-body-light" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ textAlign: 'center', color: 'var(--text-muted)' }}>
                  <div style={{ marginBottom: 8, fontSize: 13 }}>No preview available for this file type.</div>
                  <div style={{ fontSize: 12, marginBottom: 24, fontFamily: 'monospace' }}>{file.filename}</div>
                  <a href={url} download={file.filename} className="btn primary">Download</a>
                </div>
              </div>
            )}
          </>
        )}

        {/* ── Technical tab ── */}
        {tab === 'technical' && (
          <div className="modal-body modal-body-light" style={{ padding: 0 }}>
            <MediaInfoPanel fileId={file.id} />
          </div>
        )}

      </div>
    </div>
  )
}
