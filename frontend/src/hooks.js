import { useState, useEffect, useCallback } from 'react'
import { api } from './api'

// Polls AI status: fast while the model is loading or a batch runs, slow otherwise.
export function useAIStatus() {
  const [status, setStatus] = useState(null)
  const [err, setErr]       = useState(null)

  const refresh = useCallback(async () => {
    try {
      const s = await api.aiStatus()
      setStatus(s)
      setErr(null)
      return s
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  const busy = status?.state === 'loading' || ['queued', 'running', 'cancelling'].includes(status?.batch?.status)

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, busy ? 1500 : 8000)
    return () => clearInterval(t)
  }, [refresh, busy])

  return { status, err, refresh, setStatus }
}

export function useElapsed(since) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!since) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [since])
  return since ? Math.max(0, Math.round((now - new Date(since).getTime()) / 1000)) : 0
}
