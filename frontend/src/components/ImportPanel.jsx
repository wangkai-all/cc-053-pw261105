import React, { useState } from 'react'
import { api } from '../api.js'
import { EXAMPLE_PAYLOAD, SAMPLES } from '../samples.js'

const STATUS_LABEL = {
  applied: '已应用', pending: '待发布', duplicate: '重复跳过',
  conflict: '内容冲突', error: '校验失败',
}

export default function ImportPanel({ onDone }) {
  const [text, setText] = useState(EXAMPLE_PAYLOAD)
  const [results, setResults] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const run = async (fn) => {
    setBusy(true); setError(null)
    try {
      const r = await fn()
      if (r) setResults((prev) => [...(prev || []), ...r])
      onDone()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const importText = () => run(async () => {
    let payload
    try {
      payload = JSON.parse(text)
    } catch {
      throw new Error('JSON 解析失败，请检查格式')
    }
    const events = Array.isArray(payload) ? payload : payload.events
    if (!Array.isArray(events)) throw new Error('需要 events 数组')
    const r = await api.importEvents(events)
    return r.results.map((x) => ({ ...x, _k: Math.random() }))
  })

  const loadSample = (sample) => run(async () => {
    const out = []
    for (const step of sample.steps) {
      const r = await api.importEvents(step)
      out.push(...r.results)
    }
    return out.map((x) => ({ ...x, _k: Math.random() }))
  })

  const resetAll = () => run(async () => {
    await api.reset()
    setResults(null)
    return null
  })

  return (
    <section className="import-panel">
      <h2>导入事件</h2>
      <div className="samples">
        {SAMPLES.map((s) => (
          <button key={s.key} className="sample" disabled={busy}
                  title={s.desc} onClick={() => loadSample(s)}>
            {s.name}
          </button>
        ))}
        <button className="sample danger" disabled={busy} onClick={resetAll}>
          清空全部数据
        </button>
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)}
                rows={10} spellCheck={false} />
      <div className="import-actions">
        <button className="primary" disabled={busy} onClick={importText}>
          导入 JSON 事件
        </button>
        <span className="hint">
          支持 claim / consume / refund；同一编号内容相同只处理一次，内容不同报冲突；
          晚到事件自动生成重算草稿。
        </span>
      </div>
      {error && <div className="error">⚠ {error}</div>}
      {results && results.length > 0 && (
        <ul className="import-results">
          {results.map((r, i) => (
            <li key={r._k || i} className={`ir-${r.status}`}>
              <span className="mono">{r.event_id ?? '—'}</span>
              <span className="tag">{STATUS_LABEL[r.status] || r.status}</span>
              <span className="detail">{r.detail}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
