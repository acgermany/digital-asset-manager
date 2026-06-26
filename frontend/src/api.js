const BASE = '/api'

async function req(path, options = {}) {
  const res = await fetch(BASE + path, options)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function jreq(path, method, body) {
  const res = await fetch(BASE + path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export const api = {
  stats:          ()                   => req('/stats'),
  scanStatus:     ()                   => req('/scan/status'),
  startScan:      (path, workers = 4)  => req(`/scan/start?scan_path=${encodeURIComponent(path)}&workers=${workers}`, { method: 'POST' }),
  cancelScan:     ()                   => req('/scan/cancel', { method: 'POST' }),
  duplicates:     (limit, offset)      => req(`/duplicates?limit=${limit}&offset=${offset}`),
  autoMark:       (sha256)             => req(`/duplicates/${sha256}/auto-mark`, { method: 'POST' }),
  markFile:       (id, mark)           => req(`/files/${id}/mark?mark=${mark}`, { method: 'POST' }),
  files:          (params = {})        => req('/files?' + new URLSearchParams(params)),
  browse:         (path)               => req('/browse?' + new URLSearchParams({ path })),
  // Segments
  segments:       (fileId)             => req(`/files/${fileId}/segments`),
  createSegment:  (fileId, data)       => jreq(`/files/${fileId}/segments`, 'POST', data),
  updateSegment:  (segId, data)        => jreq(`/segments/${segId}`, 'PATCH', data),
  deleteSegment:  (segId)              => req(`/segments/${segId}`, { method: 'DELETE' }),
}
