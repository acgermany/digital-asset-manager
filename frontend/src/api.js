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
  activeJob:      ()                   => req('/jobs/active'),
  startScan:      (path, workers = 4)  => jreq('/jobs', 'POST', { type: 'scan', params: { scan_path: path, workers } }),
  cancelJob:      (id)                 => req(`/jobs/${id}/cancel`, { method: 'POST' }),
  recentJobs:     (limit = 5)          => req(`/jobs?limit=${limit}`),
  duplicates:     (limit, offset)      => req(`/duplicates?limit=${limit}&offset=${offset}`),
  autoMark:       (sha256)             => req(`/duplicates/${sha256}/auto-mark`, { method: 'POST' }),
  markFile:       (id, mark)           => req(`/files/${id}/mark?mark=${mark}`, { method: 'POST' }),
  files:          (params = {})        => req('/files?' + new URLSearchParams(params)),
  browse:         (path)               => req('/browse?' + new URLSearchParams({ path })),
  // Media info
  mediainfo:      (fileId)             => req(`/files/${fileId}/mediainfo`),
  queueMediainfo: (fileId)             => req(`/files/${fileId}/mediainfo/scan`, { method: 'POST' }),
  // Workflows
  workflows:      ()                   => req('/workflows'),
  runWorkflow:    (workflow, file_ids) => jreq('/workflows/run', 'POST', { workflow, file_ids }),
  // Segments
  segments:          (fileId)                            => req(`/files/${fileId}/segments`),
  createSegment:     (fileId, data)                      => jreq(`/files/${fileId}/segments`, 'POST', data),
  updateSegment:     (segId, data)                       => jreq(`/segments/${segId}`, 'PATCH', data),
  deleteSegment:     (segId)                             => req(`/segments/${segId}`, { method: 'DELETE' }),
  // Ingest
  ingestStats:       ()                                  => req('/ingest/stats'),
  triggerIngest:     (file_path, ingest_source, initials) => jreq('/ingest', 'POST', { file_path, ingest_source, initials }),
  // Staged review
  staged:            (params = {})                       => req('/staged?' + new URLSearchParams(params)),
  setStagedPath:     (id, path)                          => jreq(`/staged/${id}/path`, 'PATCH', { path }),
  setStagedCategory: (id, category)                      => jreq(`/staged/${id}/category`, 'PATCH', { category }),
  approveStaged:     (id)                                => req(`/staged/${id}/approve`, { method: 'POST' }),
  rejectStaged:      (id)                                => req(`/staged/${id}/reject`, { method: 'POST' }),
  approveAllStaged:  ()                                  => req('/staged/approve-all', { method: 'POST' }),
  // Watcher sources
  watcherSources:    ()                                  => req('/watcher/sources'),
  toggleWatcherSource: (id, enabled)                     => jreq(`/watcher/sources/${id}`, 'PATCH', { enabled }),
}
