import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'

// Modal folder browser over the locations the app can see.
// Paths shown and returned are host paths (what the user recognises).
export default function FolderPicker({ title, initialPath, requireWritable, onSelect, onClose, serverName }) {
  const [view, setView]       = useState(null)
  const [err, setErr]         = useState(null)
  const [newName, setNewName] = useState(null)   // null = not creating

  const open = useCallback(async (path) => {
    setErr(null)
    try {
      setView(await api.browse(path))
    } catch (e) {
      if (path) {               // remembered path vanished: fall back to the roots
        setErr(e.message)
        setView(await api.browse())
      } else {
        setErr(e.message)
      }
    }
  }, [])

  useEffect(() => { open(initialPath || null) }, [open, initialPath])

  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const createFolder = async () => {
    if (!newName?.trim()) return
    try {
      setView(await api.mkdir(view.path, newName.trim()))
      setNewName(null)
      setErr(null)
    } catch (e) {
      setErr(e.message)
    }
  }

  const atRoots = view && !view.path
  const canSelect = view && view.path && (!requireWritable || view.writable)

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal picker" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">{title || 'Choose a folder'}</span>
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>

        <div className="picker-path">
          {!atRoots && (
            <button className="btn sm" onClick={() => open(view?.parent || null)}>↑ Up</button>
          )}
          <span className="picker-path-text">{atRoots ? 'Locations' : view?.host_path}</span>
          {!atRoots && view && (
            <span className="picker-meta">
              {view.file_count} file{view.file_count === 1 ? '' : 's'}
              {!view.writable && ' · read-only'}
            </span>
          )}
        </div>

        <div className="picker-note">
          These are folders on <strong>{serverName || 'the server'}</strong>, the computer running the DAM — not the device you’re using.
          To bring files in from this device, use <strong>Upload</strong> (Workflows → Ingest). To use a folder that lives on another
          computer, share it over the network and add it in Configure → <strong>Network shares</strong>; it then appears here.
        </div>

        {err && <div className="picker-err">{err}</div>}

        <div className="picker-list">
          {view && view.dirs.length === 0 && (
            <div className="picker-empty">{atRoots ? 'No locations available' : 'No subfolders'}</div>
          )}
          {view?.dirs.map(d => (
            <button key={d.path} className="picker-item" onClick={() => open(d.path)}>
              <span className="picker-icon">{d.kind === 'network' ? '🖧' : d.kind === 'drives' ? '💽' : d.kind === 'host' ? '💻' : '📁'}</span>
              <span className="picker-name">{d.name}</span>
              {atRoots && <span className="picker-host">{d.host_path}</span>}
              <span className="picker-arrow">›</span>
            </button>
          ))}
        </div>

        <div className="picker-footer">
          {!atRoots && view?.writable && (
            newName === null
              ? <button className="btn sm" onClick={() => setNewName('')}>New folder</button>
              : <span className="picker-new">
                  <input
                    className="search"
                    autoFocus
                    placeholder="Folder name"
                    value={newName}
                    onChange={e => setNewName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') createFolder(); if (e.key === 'Escape') setNewName(null) }}
                  />
                  <button className="btn sm" onClick={createFolder}>Create</button>
                  <button className="btn sm" onClick={() => setNewName(null)}>Cancel</button>
                </span>
          )}
          <span style={{ flex: 1 }} />
          {requireWritable && view?.path && !view.writable && (
            <span className="picker-meta">This folder is read-only</span>
          )}
          <button className="btn sm" onClick={onClose}>Cancel</button>
          <button className="btn sm primary" disabled={!canSelect} onClick={() => onSelect(view.host_path)}>
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}
