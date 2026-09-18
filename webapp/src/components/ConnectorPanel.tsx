import { useState } from 'react'
import type { ConnectorRow } from '../api/types'

export default function ConnectorPanel({
  connectors,
  saving,
  deletingId,
  onCreate,
  onDelete,
}: {
  connectors: ConnectorRow[]
  saving: boolean
  deletingId: number | null
  onCreate: (body: {
    name: string
    url: string
    oauth_client_id?: string
    oauth_client_secret?: string
  }) => Promise<void>
  onDelete: (id: number) => Promise<void>
}) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [oauthClientId, setOauthClientId] = useState('')
  const [oauthClientSecret, setOauthClientSecret] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !url.trim()) return
    await onCreate({
      name: name.trim(),
      url: url.trim(),
      oauth_client_id: oauthClientId.trim() || undefined,
      oauth_client_secret: oauthClientSecret.trim() || undefined,
    })
    setName('')
    setUrl('')
    setOauthClientId('')
    setOauthClientSecret('')
    setShowAdvanced(false)
  }

  return (
    <section className="tg-connectors">
      <header className="tg-pos-head">
        <div className="tg-pos-title">
          <strong>Add custom connector</strong>
          <span className="tg-pos-sub">Connect Claude to your data and tools.</span>
        </div>
      </header>

      <form className="tg-connector-form" onSubmit={handleSubmit}>
        <label className="tg-connector-field">
          <span>Name</span>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="My MCP server" />
        </label>
        <label className="tg-connector-field">
          <span>Remote MCP server URL</span>
          <input
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://example.com/mcp"
          />
        </label>

        <button
          type="button"
          className="tg-connector-advanced"
          onClick={() => setShowAdvanced(v => !v)}
        >
          Advanced settings
        </button>

        {showAdvanced && (
          <div className="tg-connector-advanced-fields">
            <label className="tg-connector-field">
              <span>OAuth Client ID (optional)</span>
              <input
                value={oauthClientId}
                onChange={e => setOauthClientId(e.target.value)}
                placeholder="client-id"
              />
            </label>
            <label className="tg-connector-field">
              <span>OAuth Client Secret (optional)</span>
              <input
                type="password"
                value={oauthClientSecret}
                onChange={e => setOauthClientSecret(e.target.value)}
                placeholder="client-secret"
              />
            </label>
          </div>
        )}

        <div className="tg-connector-actions">
          <button className="tg-take-btn" type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Save connector'}
          </button>
        </div>
      </form>

      {connectors.length === 0 ? (
        <div className="tg-pos-empty">No custom connectors saved yet.</div>
      ) : (
        <ul className="tg-alert-list">
          {connectors.map(connector => (
            <li key={connector.id} className="tg-alert tg-alert--live">
              <div className="tg-alert-top">
                <span className="tg-alert-src">{connector.name}</span>
                <span className="tg-alert-state tg-alert-state--live">
                  {connector.enabled ? 'enabled' : 'disabled'}
                </span>
                <button
                  className="tg-close-btn"
                  onClick={() => void onDelete(connector.id)}
                  disabled={deletingId === connector.id}
                >
                  {deletingId === connector.id ? 'Removing…' : 'Remove'}
                </button>
              </div>
              <div className="tg-alert-note">
                {connector.url}
                {connector.oauth_client_id && <span> · OAuth ID set</span>}
                {connector.has_secret && <span> · secret stored</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
