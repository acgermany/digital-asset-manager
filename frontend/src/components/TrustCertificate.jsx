// How to make devices trust the app's private certificate authority (one-time per device).
export default function TrustCertificate() {
  return (
    <div className="trust">
      <div className="cfg-help" style={{ marginTop: 0 }}>
        The app uses its own certificate authority, which can only vouch for <code>.local</code> names and private
        addresses. Until a device trusts it, the browser shows a warning. Install it once per device:
      </div>
      <a className="btn sm primary" href="/dam-ca.crt" download="DAM-Local-CA.crt" style={{ margin: '10px 0', display: 'inline-block' }}>
        Download certificate
      </a>
      <details>
        <summary>Mac</summary>
        <ol>
          <li>Open the downloaded <code>DAM-Local-CA.crt</code>; Keychain Access adds it to <strong>login</strong>.</li>
          <li>Double-click “DAM Local CA”, expand <strong>Trust</strong>, set “When using this certificate” to <strong>Always Trust</strong>, close and enter your password.</li>
        </ol>
      </details>
      <details>
        <summary>iPhone / iPad</summary>
        <ol>
          <li>In Safari, open <code>http://dam.local/dam-ca.crt</code> and allow the profile download.</li>
          <li>Settings → <strong>Profile Downloaded</strong> → Install.</li>
          <li>Settings → General → About → <strong>Certificate Trust Settings</strong> → turn on “DAM Local CA”.</li>
        </ol>
      </details>
      <details>
        <summary>Windows</summary>
        <ol>
          <li>Open the downloaded file → <strong>Install Certificate</strong> → Current User.</li>
          <li>Choose “Place all certificates in the following store” → <strong>Trusted Root Certification Authorities</strong> → Finish.</li>
        </ol>
      </details>
      <details>
        <summary>Android</summary>
        <ol><li>Settings → Security → Encryption &amp; credentials → <strong>Install a certificate</strong> → CA certificate → pick the file.</li></ol>
      </details>
      <div className="cfg-help">Firefox keeps its own list: Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import.</div>
    </div>
  )
}
