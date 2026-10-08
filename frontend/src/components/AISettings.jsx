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
      <label className="cfg-toggle">
        <input type="checkbox" checked={!!s.describe_use_transcript} onChange={e => save({ describe_use_transcript: e.target.checked })} />
        Give the video summarizer the speech transcript (when there is one)
      </label>
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

      <TranscriptionSettings s={s} save={save} />
    </>
  )
}

function TranscriptionSettings({ s, save }) {
  const [url, setUrl]   = useState(s.transcribe_url)
  const [test, setTest] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => setUrl(s.transcribe_url), [s.transcribe_url])
  const kinds = s.transcribe_kinds || []
  const toggleKind = k => save({ transcribe_kinds: kinds.includes(k) ? kinds.filter(x => x !== k) : [...kinds, k] })

  const connect = async () => {
    setBusy(true)
    setTest(null)
    if (url === s.transcribe_url || await save({ transcribe_url: url }, 'Transcription server saved')) {
      try { setTest(await api.testTranscribe()) } catch (e) { setTest({ reachable: false, error: e.message }) }
    }
    setBusy(false)
  }

  return (
    <>
      <div className="cfg-subhead">Transcripts (Whisper speech-to-text)</div>
      <div className="cfg-help" style={{ marginTop: 0, marginBottom: 10 }}>
        Audio from videos and recordings is sent to a Whisper server on your Mac. Transcripts are searchable and help video summaries.
      </div>
      <div className="cfg-inline" style={{ paddingTop: 0 }}>
        <label>Server URL
          <input className="search" style={{ width: 300 }} value={url} onChange={e => setUrl(e.target.value)} />
        </label>
        <label>Server type
          <select className="search" value={s.transcribe_api} onChange={e => save({ transcribe_api: e.target.value })}>
            <option value="whispercpp">whisper.cpp server (/inference)</option>
            <option value="openai">OpenAI-compatible (/v1/audio/transcriptions)</option>
          </select>
        </label>
        <button className="btn sm primary" onClick={connect} disabled={busy}>{busy ? 'Testing…' : 'Save & test'}</button>
        {test && (test.reachable ? <Status ok>Server reachable</Status> : <Status>Not reachable</Status>)}
      </div>
      {test?.error && <div className="cfg-help">{test.error}</div>}
      <div className="cfg-inline">
        {s.transcribe_api === 'openai' && (
          <label>Model name
            <input className="search" style={{ width: 160 }} defaultValue={s.transcribe_model} key={s.transcribe_model}
                   onBlur={e => e.target.value !== s.transcribe_model && save({ transcribe_model: e.target.value })} />
          </label>
        )}
        <label>Language
          <input className="search" style={{ width: 80 }} defaultValue={s.transcribe_language} key={s.transcribe_language}
                 title="auto, or a code like en, es, fr"
                 onBlur={e => e.target.value !== s.transcribe_language && save({ transcribe_language: e.target.value })} />
        </label>
        <NumberSetting label="Skip recordings longer than" value={s.transcribe_max_minutes} min={1} max={600} suffix="min"
                       onSave={v => save({ transcribe_max_minutes: v })} />
      </div>
      <div className="cfg-inline">
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={kinds.includes('video')} onChange={() => toggleKind('video')} /> Transcribe videos
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={kinds.includes('audio')} onChange={() => toggleKind('audio')} /> Transcribe audio (voice memos)
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={!!s.transcribe_auto} onChange={e => save({ transcribe_auto: e.target.checked }, e.target.checked ? 'New recordings will be transcribed automatically' : 'Auto-transcribe off')} />
          Transcribe new files automatically
        </label>
        <label className="cfg-toggle" style={{ marginTop: 0 }}>
          <input type="checkbox" checked={!!s.transcribe_critical} onChange={e => save({ transcribe_critical: e.target.checked })} />
          Include critical (financial) recordings
        </label>
      </div>
      <div className="cfg-help">
        The server on this Mac: <code>whisper-server -m ~/whisper-models/ggml-large-v3-turbo-q5_0.bin --host 127.0.0.1 --port 8178 -l auto</code>.
        Once the app runs on the Linux box, start it with <code>--host 0.0.0.0</code> and point this URL at the Mac.
      </div>
    </>
  )
}
