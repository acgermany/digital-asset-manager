import { useState, useEffect } from 'react'
import { api } from '../api'

export default function SecuritySettings({ notify }) {
  const [status, setStatus] = useState(null)
  const [form, setForm]     = useState({ current: '', next: '', confirm: '' })
  const [err, setErr]       = useState(null)
  useEffect(() => { api.authStatus().then(setStatus).catch(() => {}) }, [])
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const submit = async (e) => {
    e.preventDefault()
    setErr(null)
    if (form.next !== form.confirm) { setErr('The new passwords don’t match'); return }
    try {
      await api.changePassword(form.current, form.next)
      setForm({ current: '', next: '', confirm: '' })
      notify('Password changed — other devices are signed out')
    } catch (e2) {
      setErr(e2.message)
    }
  }

  if (status?.managed_by_env) {
    return <div className="cfg-help" style={{ marginTop: 0 }}>The password is set by <code>APP_PASSWORD</code> in the server’s <code>.env</code>. Change it there and restart.</div>
  }
  return (
    <form className="cfg-inline" style={{ paddingTop: 0 }} onSubmit={submit}>
      <label>Current password
        <input className="search" type="password" style={{ width: 180 }} value={form.current} onChange={e => set('current', e.target.value)} autoComplete="current-password" />
      </label>
      <label>New password
        <input className="search" type="password" style={{ width: 180 }} value={form.next} onChange={e => set('next', e.target.value)} autoComplete="new-password" />
      </label>
      <label>Confirm
        <input className="search" type="password" style={{ width: 180 }} value={form.confirm} onChange={e => set('confirm', e.target.value)} autoComplete="new-password" />
      </label>
      <button className="btn sm primary" type="submit" disabled={!form.current || form.next.length < 10}>Change password</button>
      {err && <span className="picker-err" style={{ padding: 0 }}>{err}</span>}
    </form>
  )
}
