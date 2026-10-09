import { useState, useEffect, useRef } from 'react'
import { fmtBytes } from '../api'
import { subscribe, enqueue, cancel, retry, clearFinished } from '../uploads'

// Walk a dropped folder (drag and drop gives entries, not files).
async function filesFromEntry(entry, dir = '') {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej))
    return [{ file, relativeDir: dir }]
  }
  if (!entry.isDirectory || entry.name.startsWith('.')) return []
  const reader = entry.createReader()
  const children = []
  for (;;) {   // readEntries returns batches until empty
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej))
    if (!batch.length) break
    children.push(...batch)
  }
  const sub = dir ? `${dir}/${entry.name}` : entry.name
  return (await Promise.all(children.map(c => filesFromEntry(c, sub)))).flat()
}

const STATUS = { queued: 'Waiting', uploading: 'Uploading', done: 'Sent to ingest', failed: 'Failed', cancelled: 'Cancelled' }

export default function UploadPanel() {
  const [list, setList]           = useState([])
  const [provenance, setProv]     = useState('')
  const [over, setOver]           = useState(false)
  const filesInput  = useRef(null)
  const folderInput = useRef(null)

  useEffect(() => subscribe(setList), [])

  // Folder uploads: the top folder's name is the default provenance, subfolders are kept.
  const add = (picked, folderName) => {
    if (!picked.length) return
    const prov = provenance.trim() || folderName || 'Uploads'
    const strip = folderName ? folderName.length + 1 : 0
    enqueue(picked.map(p => ({ file: p.file, relativeDir: folderName ? p.relativeDir.slice(strip) : p.relativeDir })), prov)
  }

  const onFiles = e => { add([...e.target.files].map(f => ({ file: f, relativeDir: '' }))); e.target.value = '' }
  const onFolder = e => {
    const files = [...e.target.files].filter(f => !f.name.startsWith('.'))
    if (!files.length) return
    const top = files[0].webkitRelativePath.split('/')[0]
    add(files.map(f => ({ file: f, relativeDir: f.webkitRelativePath.split('/').slice(0, -1).join('/') })), top)
    e.target.value = ''
  }
  const onDrop = async e => {
    e.preventDefault()
    setOver(false)
    const entries = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry?.()).filter(Boolean)
    for (const entry of entries) {
      const files = (await filesFromEntry(entry)).filter(f => !f.file.name.startsWith('.'))
      add(files, entry.isDirectory ? entry.name : null)
    }
  }

  const finished = list.filter(i => !['queued', 'uploading'].includes(i.status)).length

  return (
    <div className="card upload-card">
      <div className="cfg-section-head" style={{ marginBottom: 10 }}>
        <div>
          <div className="cfg-section-title">Upload from this device</div>
          <div className="cfg-section-sub">Files go to the hot folder and through the normal ingest: checksums, duplicates, library.</div>
        </div>
      </div>
      <div
        className={`dropzone${over ? ' over' : ''}`}
        onDragOver={e => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <div>Drop files or folders here</div>
        <div className="dropzone-actions">
          <button className="btn sm" onClick={() => filesInput.current.click()}>Choose files</button>
          <button className="btn sm" onClick={() => folderInput.current.click()}>Choose folder</button>
          <label className="cfg-muted">Provenance
            <input className="search" style={{ width: 200, marginLeft: 8 }} placeholder="folder name, or “Uploads”"
                   value={provenance} onChange={e => setProv(e.target.value)} />
          </label>
        </div>
        <input ref={filesInput} type="file" multiple hidden onChange={onFiles} />
        <input ref={folderInput} type="file" webkitdirectory="" directory="" hidden onChange={onFolder} />
      </div>

      {list.length > 0 && (
        <div className="upload-list">
          {list.slice().reverse().slice(0, 200).map(i => (
            <div key={i.id} className="upload-row">
              <div className="upload-name" title={`${i.provenance}/${i.relativeDir ? i.relativeDir + '/' : ''}${i.name}`}>
                {i.name}
                <span className="cfg-muted"> → {i.provenance}{i.relativeDir ? `/${i.relativeDir}` : ''}</span>
              </div>
              <div className="upload-bar"><div style={{ width: `${i.size ? Math.round(i.sent / i.size * 100) : 0}%` }} className={`upload-fill ${i.status}`} /></div>
              <div className="upload-meta">
                {i.status === 'uploading'
                  ? `${fmtBytes(i.sent)} / ${fmtBytes(i.size)}${i.rate ? ` · ${fmtBytes(i.rate)}/s` : ''}`
                  : `${STATUS[i.status]} · ${fmtBytes(i.size)}`}
                {i.error && <span className="upload-err" title={i.error}> · {i.error}</span>}
              </div>
              {(i.status === 'queued' || i.status === 'uploading') && <button className="btn sm" onClick={() => cancel(i.id)}>Cancel</button>}
              {(i.status === 'failed' || i.status === 'cancelled') && <button className="btn sm" onClick={() => retry(i.id)}>Retry</button>}
            </div>
          ))}
          {finished > 0 && <button className="btn sm" style={{ marginTop: 8 }} onClick={clearFinished}>Clear finished</button>}
        </div>
      )}
    </div>
  )
}
