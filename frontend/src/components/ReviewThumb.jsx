import { useState, useEffect } from 'react'

const ICON = { image: '🖼', video: '🎬', audio: '🎵', document: '📄', other: '📦' }
const TEXT_EXT = /\.(txt|csv|md|json|xml|log|qif|ofx|qfx)$/i

// A preview of the file under review: thumbnail for images, video frames and PDFs; the first
// lines for text; an icon otherwise. Click to open it in the viewer.
export default function ReviewThumb({ file, kind, caption, onOpen }) {
  const [failed, setFailed] = useState(false)
  const [text, setText]     = useState(null)
  const isText = TEXT_EXT.test(file.filename)
  const hasThumb = !failed && (kind === 'image' || kind === 'video' || /\.pdf$/i.test(file.filename))

  useEffect(() => {
    if (!isText) return
    let alive = true
    fetch(`/api/files/${file.id}/view`).then(r => r.ok ? r.text() : '').then(t => alive && setText(t.slice(0, 600))).catch(() => {})
    return () => { alive = false }
  }, [file.id, isText])

  return (
    <button className="review-thumb" onClick={onOpen} title={`Open ${file.filename}`}>
      <div className="review-thumb-img">
        {hasThumb
          ? <img src={`/api/files/${file.id}/thumbnail`} alt="" onError={() => setFailed(true)} />
          : isText && text !== null
            ? <pre className="review-thumb-text">{text || '(empty file)'}</pre>
            : <span className="thumb-icon">{ICON[kind] || ICON.other}</span>}
        {kind === 'video' && hasThumb && <span className="thumb-play">▶</span>}
      </div>
      {caption && <div className="review-thumb-caption">{caption}</div>}
    </button>
  )
}
