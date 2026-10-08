const BASE = '/api'

async function handle(res) {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return res.json()
}

async function req(path, options = {}) {
  return handle(await fetch(BASE + path, options))
}

async function jreq(path, method, body) {
  return handle(await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }))
}

export const api = {
  health:         ()                   => req('/health'),
  stats:          ()                   => req('/stats'),
  scanStatus:     ()                   => req('/scan/status'),
  startScan:      (path, workers = 2)  => req(`/scan/start?scan_path=${encodeURIComponent(path)}&workers=${workers}`, { method: 'POST' }),
  cancelScan:     ()                   => req('/scan/cancel', { method: 'POST' }),
  duplicates:     (limit, offset)      => req(`/duplicates?limit=${limit}&offset=${offset}`),
  autoMark:       (sha256)             => req(`/duplicates/${sha256}/auto-mark`, { method: 'POST' }),
  markFile:       (id, mark)           => req(`/files/${id}/mark?mark=${mark}`, { method: 'POST' }),
  files:          (params = {})        => req('/files?' + new URLSearchParams(params)),
  // Segments
  segments:       (fileId)             => req(`/files/${fileId}/segments`),
  createSegment:  (fileId, data)       => jreq(`/files/${fileId}/segments`, 'POST', data),
  updateSegment:  (segId, data)        => jreq(`/segments/${segId}`, 'PATCH', data),
  deleteSegment:  (segId)              => req(`/segments/${segId}`, { method: 'DELETE' }),
  // Configure
  config:         ()                   => req('/config'),
  updateConfig:   (changes)            => jreq('/config', 'PUT', changes),
  browse:         (path)               => req('/locations/browse' + (path ? '?' + new URLSearchParams({ path }) : '')),
  mkdir:          (path, name)         => jreq('/locations/mkdir', 'POST', { path, name }),
  addMount:       (data)               => jreq('/mounts', 'POST', data),
  mountShare:     (id)                 => req(`/mounts/${id}/mount`, { method: 'POST' }),
  unmountShare:   (id)                 => req(`/mounts/${id}/unmount`, { method: 'POST' }),
  deleteMount:    (id)                 => req(`/mounts/${id}`, { method: 'DELETE' }),
  testAI:         ()                   => req('/ai/test', { method: 'POST' }),
  // Workflows
  ingests:        (params = {})        => req('/ingests?' + new URLSearchParams(params)),
  retryIngest:    (id)                 => req(`/ingests/${id}/retry`, { method: 'POST' }),
  reviews:        (params = {})        => req('/reviews?' + new URLSearchParams(params)),
  resolveReview:  (id, resolution, tier) => jreq(`/reviews/${id}/resolve`, 'POST', { resolution, tier }),
  jobsSummary:    ()                   => req('/jobs/summary'),
  // AI / search
  aiStatus:       ()                   => req('/ai/status'),
  enableSemantic: ()                   => req('/ai/semantic/enable', { method: 'POST' }),
  disableSemantic:()                   => req('/ai/semantic/disable', { method: 'POST' }),
  startEmbed:     (reembed = false)    => jreq('/ai/embed', 'POST', { reembed }),
  cancelEmbed:    ()                   => req('/ai/embed/cancel', { method: 'POST' }),
  search:         (params)             => req('/search?' + new URLSearchParams(params)),
  // Viewer
  viewInfo:       (fileId)             => req(`/files/${fileId}/viewinfo`),
  retryProxy:     (fileId)             => req(`/files/${fileId}/proxy/retry`, { method: 'POST' }),
}

export function fmtBytes(bytes) {
  if (!bytes) return '0 B'
  if (bytes >= 1e12) return (bytes / 1e12).toFixed(2) + ' TB'
  if (bytes >= 1e9)  return (bytes / 1e9).toFixed(2)  + ' GB'
  if (bytes >= 1e6)  return (bytes / 1e6).toFixed(2)  + ' MB'
  if (bytes >= 1e3)  return (bytes / 1e3).toFixed(1)  + ' KB'
  return bytes + ' B'
}
