const j = async (r) => {
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `${r.status} ${r.statusText}`)
  }
  return r.json()
}
const enc = encodeURIComponent

export const api = {
  members: () => fetch('/api/members').then(j),
  ledger: (m) => fetch(`/api/members/${enc(m)}/ledger`).then(j),
  draft: (m) => fetch(`/api/members/${enc(m)}/draft`).then(j),
  versions: (m) => fetch(`/api/members/${enc(m)}/versions`).then(j),
  version: (m, v) => fetch(`/api/members/${enc(m)}/versions/${v}`).then(j),
  importEvents: (events) =>
    fetch('/api/events/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events }),
    }).then(j),
  publish: (m) => fetch(`/api/members/${enc(m)}/draft/publish`, { method: 'POST' }).then(j),
  discard: (m) => fetch(`/api/members/${enc(m)}/draft/discard`, { method: 'POST' }).then(j),
  reset: () => fetch('/api/demo/reset', { method: 'POST' }).then(j),
}
