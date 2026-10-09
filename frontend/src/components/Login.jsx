import { useState } from 'react'
import { api } from '../api'

export default function Login({ mode, onDone }) {
  const [code, setCode]         = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm]   = useState('')
  const [err, setErr]           = useState(null)
  const [busy, setBusy]         = useState(false)
  const setup = mode === 'setup'

  const submit = async (e) => {
    e.preventDefault()
    setErr(null)
    if (setup && password !== confirm) { setErr('The passwords don’t match'); return }
    setBusy(true)
    try {
      setup ? await api.setup(code, password) : await api.login(password)
      onDone()
    } catch (e2) {
      setErr(e2.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="topbar-logo" style={{ fontSize: 20, marginBottom: 4 }}>DAM<span>·</span>nas</div>
        <div className="section-sub" style={{ marginBottom: 18 }}>
          {setup ? 'Create the password that protects this library.' : 'Sign in to your library.'}
        </div>
        {setup && (
          <label className="login-field">Setup code
            <input className="search" autoFocus value={code} onChange={e => setCode(e.target.value)} placeholder="XXXX-XXXX-XXXX" autoComplete="off" />
            <span className="cfg-help">On the computer running the app: <code>docker compose logs app | grep "setup code"</code>, or the file <code>data/secrets/setup-code</code>.</span>
          </label>
        )}
        <label className="login-field">{setup ? 'New password' : 'Password'}
          <input className="search" type="password" autoFocus={!setup} value={password} onChange={e => setPassword(e.target.value)}
                 autoComplete={setup ? 'new-password' : 'current-password'} />
        </label>
        {setup && (
          <label className="login-field">Confirm password
            <input className="search" type="password" value={confirm} onChange={e => setConfirm(e.target.value)} autoComplete="new-password" />
            <span className="cfg-help">At least 10 characters.</span>
          </label>
        )}
        {err && <div className="picker-err" style={{ padding: '0 0 10px' }}>{err}</div>}
        <button className="btn primary" type="submit" disabled={busy || !password || (setup && !code)} style={{ width: '100%' }}>
          {busy ? '…' : setup ? 'Create password' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
