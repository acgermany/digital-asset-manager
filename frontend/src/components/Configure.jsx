import { useState, useEffect, useCallback } from 'react'
import { api, fmtBytes } from '../api'
import FolderPicker from './FolderPicker'
import AISettings from './AISettings'

function Status({ ok, warn, children }) {
  return <span className={`status-chip ${ok ? 'ok' : warn ? 'warn' : 'bad'}`}>{children}</span>
}

function PathStatus({ status, library }) {
  if (!status?.path) return <Status warn>Not set</Status>
  if (!status.visible) return <Status>Not visible to the app</Status>
  if (!status.exists) return <Status>Missing — drive or share not mounted?</Status>
  if (!status.writable) return <Status>Read-only</Status>
  if (library && !status.marker) return <Status warn>Not marked as library</Status>
  return <Status ok>Ready</Status>
}

function Section({ title, sub, children, actions }) {
  return (
    <div className="card cfg-section">
      <div className="cfg-section-head">
        <div>
          <div className="cfg-section-title">{title}</div>
          {sub && <div className="cfg-section-sub">{sub}</div>}
        </div>
        {actions}
      </div>
      {children}
    </div>
  )
}

function FolderSetting({ label, help, value, status, library, onChoose, onClear }) {
  return (
    <div className="cfg-folder">
      <div className="cfg-folder-label">
        <span>{label}</span>
        <PathStatus status={status} library={library} />
      </div>
      <div className="cfg-folder-row">
        <span className={`cfg-path${value ? '' : ' empty'}`}>{value || 'No folder chosen'}</span>
        <button className="btn sm" onClick={onChoose}>{value ? 'Change…' : 'Choose…'}</button>
        {value && onClear && <button className="btn sm" onClick={onClear}>Clear</button>}
      </div>
      {help && <div className="cfg-help">{help}</div>}
    </div>
  )
}

const EMPTY_SHARE = { name: '', protocol: 'smb', server: '', share: '', username: '', password: '', read_only: true, auto_mount: true, options: '' }

function AddShareForm({ onAdded, onCancel }) {
  const [form, setForm] = useState(EMPTY_SHARE)
  const [busy, setBusy] = useState(false)
  const [err, setErr]   = useState(null)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    try {
      const m = await api.addMount({ ...form, username: form.username || null, password: form.password || null, options: form.options || null })
      onAdded(m)
    } catch (e2) {
      setErr(e2.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="share-form" onSubmit={submit}>
      <div className="share-grid">
        <label>Type
          <select className="search" value={form.protocol} onChange={e => set('protocol', e.target.value)}>
            <option value="smb">SMB (Windows / Mac / most NAS)</option>
            <option value="nfs">NFS</option>
          </select>
        </label>
        <label>Server
          <input className="search" placeholder="nas.local or 192.168.1.20" value={form.server} onChange={e => set('server', e.target.value)} required />
        </label>
        <label>{form.protocol === 'smb' ? 'Share name' : 'Export path'}
          <input className="search" placeholder={form.protocol === 'smb' ? 'Media' : '/volume1/media'} value={form.share} onChange={e => set('share', e.target.value)} required />
        </label>
        <label>Name in the app
          <input className="search" placeholder="nas-media" value={form.name} onChange={e => set('name', e.target.value)} required />
        </label>
        {form.protocol === 'smb' && <>
          <label>Username <span className="cfg-help-inline">(blank for guest)</span>
            <input className="search" autoComplete="off" value={form.username} onChange={e => set('username', e.target.value)} />
          </label>
          <label>Password
            <input className="search" type="password" autoComplete="new-password" value={form.password} onChange={e => set('password', e.target.value)} />
          </label>
        </>}
        <label>Extra mount options <span className="cfg-help-inline">(optional, e.g. vers=3.0)</span>
          <input className="search" value={form.options} onChange={e => set('options', e.target.value)} />
        </label>
      </div>
      <div className="share-checks">
        <label><input type="checkbox" checked={form.read_only} onChange={e => set('read_only', e.target.checked)} /> Read-only (untick to use it for the hot folder or library)</label>
        <label><input type="checkbox" checked={form.auto_mount} onChange={e => set('auto_mount', e.target.checked)} /> Reconnect automatically when the app starts</label>
      </div>
      {err && <div className="picker-err">{err}</div>}
      <div className="share-actions">
        <button type="button" className="btn sm" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn sm primary" disabled={busy}>{busy ? 'Connecting…' : 'Add and connect'}</button>
      </div>
    </form>
  )
}

export default function Configure() {
  const [cfg, setCfg]         = useState(null)
  const [err, setErr]         = useState(null)
  const [notice, setNotice]   = useState(null)
  const [picker, setPicker]   = useState(null)   // { key, title, writable }
  const [adding, setAdding]   = useState(false)

  const load = useCallback(async () => {
    try {
      const c = await api.config()
      setCfg(c)
      setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = async (changes, message) => {
    try {
      setCfg(await api.updateConfig(changes))
      setErr(null)
      setNotice(message || 'Saved')
      setTimeout(() => setNotice(null), 2500)
      return true
    } catch (e) {
      setErr(e.message)
      return false
    }
  }

  const onPicked = async (hostPath) => {
    const { key } = picker
    setPicker(null)
    if (key === 'catalog_sources') {
      const list = [...(cfg.settings.catalog_sources || [])]
      if (!list.includes(hostPath)) list.push(hostPath)
      await save({ catalog_sources: list }, 'Scan source added')
    } else {
      await save({ [key]: hostPath }, `${key === 'hotfolder' ? 'Hot folder' : 'Library'} set`)
    }
  }

  const shareAction = async (fn, id) => {
    try {
      await fn(id)
      await load()
    } catch (e) {
      setErr(e.message)
    }
  }

  if (!cfg) {
    return err ? <div className="error-box">API unavailable: {err}</div> : <div style={{ color: 'var(--text-muted)' }}>Loading…</div>
  }

  const s = cfg.settings
  const st = cfg.status

  return (
    <>
      <div className="section-header">
        <div>
          <div className="section-title">Configure</div>
          <div className="section-sub">Where files come in, where they go, and what the app can see</div>
        </div>
        {notice && <span className="cfg-notice">{notice}</span>}
      </div>

      {err && <div className="error-box">{err}</div>}

      <Section title="Ingest" sub="Files dropped in the hot folder are checksummed, de-duplicated and filed into the library.">
        <FolderSetting
          label="Hot folder"
          help="Drop files here (subfolder name = provenance, e.g. Hot/Grandma/photo.jpg). Exact duplicates are removed from this folder."
          value={s.hotfolder}
          status={st.hotfolder}
          onChoose={() => setPicker({ key: 'hotfolder', title: 'Choose the hot folder', writable: true })}
          onClear={() => save({ hotfolder: '' }, 'Hot folder cleared')}
        />
        <FolderSetting
          label="Library"
          help="Accepted files are moved here as <provenance>/<year>/<month>/. A hidden .dam_library marker lets the app detect when this drive or share is not mounted."
          value={s.library}
          status={st.library}
          library
          onChoose={() => setPicker({ key: 'library', title: 'Choose the library folder', writable: true })}
        />
        <div className="cfg-inline">
          <label className="cfg-toggle">
            <input type="checkbox" checked={!!s.hotfolder_enabled} onChange={e => save({ hotfolder_enabled: e.target.checked }, e.target.checked ? 'Watching hot folder' : 'Hot folder paused')} />
            Watch the hot folder
          </label>
          <label>Exact duplicates (same name and checksum)
            <select className="search" value={s.delete_mode} onChange={e => save({ delete_mode: e.target.value })}>
              <option value="delete">Delete from hot folder</option>
              <option value="trash">Move to hot folder/_trash</option>
            </select>
          </label>
          <label>Wait until unchanged for
            <span className="cfg-num">
              <input className="search" type="number" min="1" max="600" defaultValue={s.stable_seconds}
                     onBlur={e => Number(e.target.value) !== s.stable_seconds && save({ stable_seconds: Number(e.target.value) })} />
              seconds
            </span>
          </label>
        </div>
      </Section>

      <Section
        title="Scan sources"
        sub="Folders catalogued in place (read-only) to find duplicates across your existing storage."
        actions={<button className="btn sm" onClick={() => setPicker({ key: 'catalog_sources', title: 'Add a folder to scan' })}>Add folder…</button>}
      >
        {(s.catalog_sources || []).length === 0 && <div className="cfg-empty">No scan sources yet.</div>}
        {(s.catalog_sources || []).map((p, i) => (
          <div key={p} className="cfg-list-row">
            <span className="cfg-path">{p}</span>
            <PathStatus status={{ ...st.catalog_sources[i], writable: true }} />
            <button className="btn sm" onClick={() => save({ catalog_sources: s.catalog_sources.filter(x => x !== p) }, 'Scan source removed')}>Remove</button>
          </div>
        ))}
      </Section>

      <Section title="Locations" sub="Everything the app can see on this computer. The folder pickers browse these.">
        {cfg.roots.filter(r => r.kind !== 'network').map(r => (
          <div key={r.path} className="cfg-list-row">
            <span className="loc-icon">{r.kind === 'drives' ? '💽' : '💻'}</span>
            <span className="loc-label">{r.label}</span>
            <span className="cfg-path">{r.host_path}</span>
            {r.available ? <Status ok>Available</Status> : <Status>Not available</Status>}
          </div>
        ))}
        <div className="cfg-subhead">Attached drives</div>
        {cfg.drives.length === 0 && <div className="cfg-empty">No external drives detected. Plug one in and it appears here.</div>}
        {cfg.drives.map(d => (
          <div key={d.path} className="cfg-list-row">
            <span className="loc-icon">💽</span>
            <span className="loc-label">{d.name}</span>
            <span className="cfg-path">{d.host_path}</span>
            {d.total_bytes != null && <span className="cfg-muted">{fmtBytes(d.free_bytes)} free of {fmtBytes(d.total_bytes)}</span>}
          </div>
        ))}
        <div className="cfg-help">
          Other folders on this computer are made visible in <code>docker-compose.yml</code> (<code>HOST_HOME</code> / <code>HOST_DRIVES</code>).
        </div>
      </Section>

      <Section
        title="Network shares"
        sub="Connect a NAS or a folder shared from another computer (SMB or NFS)."
        actions={!adding && <button className="btn sm" onClick={() => setAdding(true)}>Add share…</button>}
      >
        {adding && <AddShareForm onCancel={() => setAdding(false)} onAdded={() => { setAdding(false); load() }} />}
        {cfg.mounts.length === 0 && !adding && <div className="cfg-empty">No network shares.</div>}
        {cfg.mounts.map(m => (
          <div key={m.id} className="share-row">
            <div className="cfg-list-row">
              <span className="loc-icon">🖧</span>
              <span className="loc-label">{m.name}</span>
              <span className="cfg-path">{m.url}</span>
              <span className="cfg-muted">{m.read_only ? 'read-only' : 'read-write'}</span>
              {m.mounted ? <Status ok>Connected</Status> : <Status>{m.last_error ? 'Failed' : 'Disconnected'}</Status>}
              {m.mounted
                ? <button className="btn sm" onClick={() => shareAction(api.unmountShare, m.id)}>Disconnect</button>
                : <button className="btn sm" onClick={() => shareAction(api.mountShare, m.id)}>Connect</button>}
              <button className="btn sm danger" onClick={() => shareAction(api.deleteMount, m.id)}>Remove</button>
            </div>
            {m.last_error && !m.mounted && <div className="share-err">{m.last_error}</div>}
          </div>
        ))}
      </Section>

      <Section title="AI (Ollama on your Mac)" sub="Optional — ingest, duplicates and keyword search work without it.">
        <AISettings settings={s} defaults={cfg.defaults} embedDims={cfg.embed_dims} save={save} />
      </Section>

      {picker && (
        <FolderPicker
          title={picker.title}
          requireWritable={picker.writable}
          initialPath={picker.key === 'catalog_sources' ? null : (s[picker.key] || null)}
          onClose={() => setPicker(null)}
          onSelect={onPicked}
        />
      )}
    </>
  )
}
