import { useState } from 'react'
import { api } from '../api'
import { useElapsed } from '../hooks'

const LABEL = {
  offline: 'Mac offline',
  unloaded: 'Model not loaded',
  loading: 'Loading model…',
  ready: 'Ready',
}

// Switch for semantic search. Enabling loads the embedding model on the Mac.
export default function SemanticToggle({ status, onChange }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState(null)
  const elapsed = useElapsed(status?.state === 'loading' ? status.loading_since : null)

  if (!status) return null
  const on = status.semantic_enabled
  const unavailable = !status.reachable || !status.model_installed

  const toggle = async () => {
    setBusy(true)
    setErr(null)
    try {
      onChange(on ? await api.disableSemantic() : await api.enableSemantic())
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  const tone = status.state === 'ready' && on ? 'ok' : status.state === 'loading' ? 'warn' : unavailable ? 'bad' : 'idle'

  return (
    <div className="semantic">
      <button
        className={`switch${on ? ' on' : ''}`}
        onClick={toggle}
        disabled={busy || (!on && unavailable)}
        title={unavailable ? status.last_error || 'Ollama unavailable' : on ? 'Turn off and unload the model' : 'Load the embedding model on the Mac'}
        aria-pressed={on}
      >
        <span className="switch-knob" />
      </button>
      <span className="semantic-label">Semantic search</span>
      <span className={`semantic-state ${tone}`}>
        {status.state === 'loading' && <span className="spinner sm" />}
        {LABEL[status.state] || status.state}
        {status.state === 'loading' && elapsed > 0 && ` ${elapsed}s`}
      </span>
      {(err || (!on && status.last_error && status.state !== 'ready')) && (
        <span className="semantic-err" title={err || status.last_error}>{err || status.last_error}</span>
      )}
    </div>
  )
}
