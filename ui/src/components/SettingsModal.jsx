import { useState } from 'react'

const KEY = 'drone_anthropic_api_key'

export default function SettingsModal({ onClose }) {
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(KEY) ?? '')
  const [saved, setSaved] = useState(false)

  function handleSave() {
    if (apiKey.trim()) {
      localStorage.setItem(KEY, apiKey.trim())
    } else {
      localStorage.removeItem(KEY)
    }
    setSaved(true)
    setTimeout(() => { setSaved(false); onClose() }, 800)
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-title">Settings</div>
        <div className="modal-sub">
          API key stored locally in your browser — never sent to any server.
          Used for MCP-enabled mission adaptation (optional).
        </div>

        <div className="field-group">
          <div className="field-label"><span>Anthropic API Key</span></div>
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-ant-…"
            autoComplete="off"
            spellCheck={false}
          />
          {apiKey && (
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
              Key set · {apiKey.slice(0, 10)}…
            </div>
          )}
        </div>

        <div style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6 }}>
          MCP-enabled features (weather adaptation, field report interpretation) require a key.
          Core simulation and reliability analysis work without one.
        </div>

        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" style={{ width: 'auto' }} onClick={handleSave}>
            {saved ? '✓ Saved' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
