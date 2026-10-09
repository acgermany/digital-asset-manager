// Upload queue shared by the whole app, so uploads keep going while you move between tabs.
// Each file is sent in chunks; a failed chunk is retried, and an interrupted upload resumes
// from what the server already has.
import { api } from './api'

const CONCURRENT_FILES = 2
const MAX_RETRIES = 5
let items = []
let listeners = new Set()
let seq = 0

function emit() {
  const snapshot = items.map(i => ({ ...i }))
  listeners.forEach(fn => fn(snapshot))
}

export function subscribe(fn) {
  listeners.add(fn)
  fn(items.map(i => ({ ...i })))
  return () => listeners.delete(fn)
}

export function summary(list) {
  const active = list.filter(i => i.status === 'queued' || i.status === 'uploading')
  const total = active.reduce((a, i) => a + i.size, 0)
  const sent = active.reduce((a, i) => a + i.sent, 0)
  return { active: active.length, pct: total ? Math.round(sent / total * 100) : 0 }
}

function update(id, changes) {
  items = items.map(i => (i.id === id ? { ...i, ...changes } : i))
  emit()
}

// files: [{ file: File, relativeDir: 'Trip/Day1' }]
export function enqueue(files, provenance) {
  for (const { file, relativeDir } of files) {
    if (!file.size) continue   // empty files are never ingested
    items.push({ id: ++seq, file, name: file.name, relativeDir: relativeDir || '', provenance,
                 size: file.size, sent: 0, status: 'queued', error: null, serverId: null, rate: 0 })
  }
  emit()
  pump()
}

export function cancel(id) {
  const it = items.find(i => i.id === id)
  if (!it) return
  if (it.serverId && it.status === 'uploading') api.cancelUpload(it.serverId).catch(() => {})
  update(id, { status: 'cancelled' })
  pump()
}

export function retry(id) {
  update(id, { status: 'queued', error: null })
  pump()
}

export function clearFinished() {
  items = items.filter(i => i.status === 'queued' || i.status === 'uploading')
  emit()
}

function pump() {
  const running = items.filter(i => i.status === 'uploading').length
  items.filter(i => i.status === 'queued').slice(0, Math.max(0, CONCURRENT_FILES - running))
    .forEach(i => { update(i.id, { status: 'uploading' }); run(i.id) })
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function run(id) {
  const get = () => items.find(i => i.id === id)
  try {
    let it = get()
    let session = it.serverId ? await api.uploadStatus(it.serverId) : null
    if (!session || session.status !== 'uploading') {
      session = await api.startUpload({ filename: it.name, size: it.size, provenance: it.provenance,
                                        relative_dir: it.relativeDir, last_modified: it.file.lastModified })
      update(id, { serverId: session.id })
    }
    let offset = session.received
    const chunk = session.chunk_size
    let failures = 0
    while (offset < it.size) {
      if (get().status !== 'uploading') return              // cancelled
      const started = performance.now()
      const end = Math.min(offset + chunk, it.size)
      try {
        const before = offset
        offset = (await api.uploadChunk(session.id, offset, it.file.slice(offset, end))).received
        failures = 0
        const secs = (performance.now() - started) / 1000
        update(id, { sent: offset, error: null, rate: secs > 0 ? (offset - before) / secs : 0 })
      } catch (e) {
        if (e.received !== undefined) { offset = e.received; continue }   // server says where it is
        if (++failures > MAX_RETRIES) throw e
        update(id, { error: `Retrying (${failures}/${MAX_RETRIES}): ${e.message}` })
        await sleep(1000 * 2 ** failures)
        try { offset = (await api.uploadStatus(session.id)).received } catch { /* keep offset */ }
      }
      it = get()
    }
    await api.completeUpload(session.id)
    update(id, { status: 'done', sent: it.size, error: null })
  } catch (e) {
    update(id, { status: 'failed', error: e.message })
  } finally {
    pump()
  }
}

// Warn before closing the tab mid-upload.
window.addEventListener('beforeunload', e => {
  if (items.some(i => i.status === 'uploading' || i.status === 'queued')) {
    e.preventDefault()
    e.returnValue = ''
  }
})
