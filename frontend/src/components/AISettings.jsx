import { useState, useEffect } from 'react'
import { api } from '../api'

function Status({ ok, warn, children }) {
  return <span className={`status-chip ${ok ? 'ok' : warn ? 'warn' : 'bad'}`}>{children}</span>
}

// Dropdown of installed Ollama models with a capability, keeping a non-installed current value visible.
function ModelSelect({ value, models, capability, onChange }) {
  const options = models.filter(m => m.capabilities.includes(capability))
  // "nomic-embed-text" and "nomic-embed-text:latest" are the same model in Ollama.
  const match = options.find(m => m.name === value || m.name === `${value}:latest`)
  const selected = match ? match.name : value
  return (
    <select className="search" value={selected} onChange={e => onChange(e.target.value)} style={{ minWidth: 220 }}>
      {!match && <option value={value}>{value}{models.length ? ' (not installed)' : ''}</option>}
      {options.map(m => (
        <option key={m.name} value={m.name}>{m.name}{m.parameters ? ` · ${m.parameters}` : ''}</option>
      ))}
    </select>
  )
}

function PromptField({ label, help, value, defaultValue, onSave }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const dirty = draft !== value
  return (
    <div className="prompt-field">
      <div className="prompt-head">
        <span>{label}</span>
        <span style={{ flex: 1 }} />
        {value !== defaultValue && !dirty && <button className="btn sm" onClick={() => onSave(defaultValue)}>Reset to default</button>}
        {dirty && <>
          <button className="btn sm" onClick={() => setDraft(value)}>Discard</button>
          <button className="btn sm primary" onClick={() => onSave(draft)}>Save prompt</button>
        </>}
      </div>
      <textarea className="prompt-text" rows={5} value={draft} onChange={e => setDraft(e.target.value)} spellCheck={false} />
      {help && <div className="cfg-help">{help}</div>}
    </div>
  )
}

function NumberSetting({ label, value, min, max, suffix, onSave }) {
  return (
    <label>{label}
      <span className="cfg-num">
        <input className="search" type="number" min={min} max={max} defaultValue={value} key={value}
               onBlur={e => Number(e.target.value) !== value && onSave(Number(e.target.value))} />
        {suffix}
      </span>
    </label>
  )
}

export default function AISettings({ settings: s, defaults, embedDims, save }) {
  const [models, setModels] = useState({ reachable: null, models: [] })
  const [url, setUrl]       = useState(s.ollama_url)
  const [test, setTest]     = useState(null)
  const [busy, setBusy]     = useState(false)

  const loadModels = async () => {
    try { setModels(await api.aiModels()) } catch (e) { setModels({ reachable: false, error: e.message, models: [] }) }
  }
  useEffect(() => { loadModels() }, [])
  useEffect(() => setUrl(s.ollama_url), [s.ollama_url])

  const connect = async () => {
    setBusy(true)
    setTest(null)
    if (await save({ ollama_url: url }, 'Ollama URL saved')) {
      await loadModels()
      try { setTest(await api.testAI()) } catch (e) { setTest({ reachable: false, error: e.message }) }
    }
    setBusy(false)
  }

  const kinds = s.describe_kinds || []
  const toggleKind = k => save({ describe_kinds: kinds.includes(k) ? kinds.filter(x => x !== k) : [...kinds, k] })

  return (
    <>
      <div className="cfg-inline" style={{ paddingTop: 0 }}>
        <label>Ollama URL
          <input className="search" style={{ width: 300 }} value={url} onChange={e => setUrl(e.target.value)} />
        </label>
        <button className="btn sm primary" onClick={connect} disabled={busy}>{busy ? 'Connecting…' : 'Save & connect'}</button>
        {models.reachable === true && <Status ok>Connected · {models.models.length} models</Status>}
        {models.reachable === false && <Status>Not reachable</Status>}
      </div>
      {(test?.error || (models.reachable === false && models.error)) && (
        <div className="cfg-help">{test?.error || models.error}</div>
      )}

      <div className="cfg-subhead">Search embeddings</div>
      <div className="cfg-inline" style={{ paddingTop: 0 }}>
        <label>Embedding model
          <ModelSelect value={s.embed_model} models={models.models} capability="embedding"
                       onChange={v => save({ embed_model: v }, 'Embedding model saved')} />
        </label>
        <label>Keep loaded for
          <input className="search" style={{ width: 80 }} defaultValue={s.embed_keep_alive} key={s.embed_keep_alive}
                 onBlur={e => e.target.value !== s.embed_keep_alive && save({ embed_keep_alive: e.target.value })} />
        </label>
      </div>
      <label className="cfg-toggle">
        <input type="checkbox" checked={!!s.embed_sensitive_text} onChange={e => save({ embed_sensitive_text: e.target.checked })} />
        Include (redacted) text of financial documents in embeddings
      </label>
      <div className="cfg-help">Vector size: {embedDims} — the model must produce vectors of this size (nomic-embed-text = 768). Changing models means re-embedding everything.</div>

      <div className="cfg-subhead">Descriptions (vision model)</div>
      <div className="cfg-help" style={{ marginTop: 0, marginBottom: 10 }}>
        Captions for photos and summaries for videos, written by a vision model on your Mac. They become searchable and improve the similarity map.
        Videos are summarized from evenly spaced frames sent together.
      </div>
      <div className="cfg-inline" style={{ paddingTop: 0 }}>
        <label>Vision model
          <ModelSelect value={s.describe_model} models={models.models} capability="vision"
                       onChange={v => save({ describe_model: v }, 'Vision model saved')} />
        </label>
        <NumberSetting label="Frames per video" value={s.describe_video_frames} min={1} max={32} onSave={v => save({ describe_video_frames: v })} />
        <NumberSetting label="Frame size" value={s.describe_frame_size} min={256} max={2048} suffix="px" onSave={v => save({ describe_frame_size: v })} />
        <NumberSetting label="Max output" value={s.describe_max_tokens} min={128} max={8192} suffix="tokens" onSave={v => save({ describe_max_tokens: v })} />
        <label>Keep loaded for
          <input className="search" style={{ width: 80 }} defaultValue={s.describe_keep_alive} key={s.describe_keep_alive}
                 onBlur={e => e.target.value !== s.describe_keep_alive && save({ describe_keep_alive: e.target.value })} />
        </label>
      </div>
      <div className="cfg-inline">
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={kinds.includes('image')} onChange={() => toggleKind('image')} /> Describe images
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={kinds.includes('video')} onChange={() => toggleKind('video')} /> Summarize videos
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={!!s.describe_auto} onChange={e => save({ describe_auto: e.target.checked }, e.target.checked ? 'New files will be described automatically' : 'Auto-describe off')} />
          Describe new files automatically
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={!!s.describe_critical} onChange={e => save({ describe_critical: e.target.checked })} />
          Include critical (financial) images
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }} title="Thinking models reason before answering. Off asks the model to skip it (not every model honours this).">
          <input type="checkbox" checked={!!s.describe_think} onChange={e => save({ describe_think: e.target.checked })} />
          Let the model think first (slower)
        </label>
      </div>
      <PromptField
        label="Image prompt"
        value={s.describe_image_prompt}
        defaultValue={defaults.describe_image_prompt}
        onSave={v => save({ describe_image_prompt: v }, 'Image prompt saved')}
        help="A final line starting with “Tags:” is turned into keywords."
      />
      <PromptField
        label="Video prompt"
        value={s.describe_video_prompt}
        defaultValue={defaults.describe_video_prompt}
        onSave={v => save({ describe_video_prompt: v }, 'Video prompt saved')}
        help="{frames} and {duration} are filled in for each video. A final line starting with “Tags:” is turned into keywords."
      />
      <div className="cfg-help">Changed the model or prompts? Use “Re-describe all” in Workflows → AI to redo existing descriptions.</div>
    </>
  )
}
