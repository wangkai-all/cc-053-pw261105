import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import BalanceCurve from './components/BalanceCurve.jsx'
import DraftPanel from './components/DraftPanel.jsx'
import ImportPanel from './components/ImportPanel.jsx'
import { BatchesTable, EventsTable } from './components/LedgerTables.jsx'

export default function App() {
  const [members, setMembers] = useState([])
  const [selected, setSelected] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [draft, setDraft] = useState(null)
  const [versions, setVersions] = useState([])
  const [viewVersion, setViewVersion] = useState(null) // 历史版本对照
  const [showImport, setShowImport] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const refreshMembers = useCallback(async () => {
    const ms = await api.members()
    setMembers(ms)
    return ms
  }, [])

  const refreshLedger = useCallback(async (memberId) => {
    if (!memberId) { setLedger(null); setDraft(null); setVersions([]); return }
    const [lg, vs] = await Promise.all([api.ledger(memberId), api.versions(memberId)])
    setLedger(lg)
    setVersions(vs)
    setDraft(lg.has_draft ? await api.draft(memberId) : null)
  }, [])

  useEffect(() => {
    refreshMembers().then((ms) => {
      if (ms.length > 0 && !selected) setSelected(ms[0].member_id)
    }).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    setViewVersion(null)
    refreshLedger(selected).catch(() => { setLedger(null); setDraft(null) })
  }, [selected, refreshLedger])

  const afterChange = async () => {
    const ms = await refreshMembers()
    let target = selected
    if (!target && ms.length > 0) target = ms[0].member_id
    if (target && !ms.find((m) => m.member_id === target)) target = ms[0]?.member_id ?? null
    if (target !== selected) { setSelected(target); return }
    await refreshLedger(target)
  }

  const act = (fn) => async () => {
    setBusy(true); setError(null)
    try { await fn(); await afterChange() }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  const viewData = useMemo(() => {
    if (viewVersion == null) return ledger
    return viewVersion
  }, [ledger, viewVersion])

  const openVersion = async (v) => {
    if (v == null) { setViewVersion(null); return }
    setViewVersion(await api.version(selected, v))
  }

  const eventsById = useMemo(() => {
    const m = {}
    for (const e of viewData?.events || []) m[e.event_id] = e
    return m
  }, [viewData])

  return (
    <div className="app">
      <header>
        <h1>会员积分台账</h1>
        <p>余额从哪来、花到哪去、退款去向，全部可回放查证</p>
        <button className="primary" onClick={() => setShowImport((v) => !v)}>
          {showImport ? '收起导入' : '导入事件 / 样例'}
        </button>
      </header>

      {showImport && <ImportPanel onDone={afterChange} />}
      {error && <div className="error">⚠ {error}</div>}

      <div className="layout">
        <aside>
          <h2>会员</h2>
          {members.length === 0 && <p className="empty">暂无会员，先导入事件或加载样例。</p>}
          {members.map((m) => (
            <button key={m.member_id}
                    className={`member ${m.member_id === selected ? 'active' : ''}`}
                    onClick={() => setSelected(m.member_id)}>
              <span className="member-id mono">{m.member_id}</span>
              {m.has_draft && <span className="tag st-pending">草稿待处理</span>}
              <span className="member-totals">
                <strong>{m.totals.remaining.toLocaleString()}</strong> 剩余 ·
                {' '}{m.totals.consumed.toLocaleString()} 已消费 ·
                {' '}{m.totals.expired.toLocaleString()} 已过期
              </span>
            </button>
          ))}
        </aside>

        <main>
          {!ledger && <p className="empty">选择左侧会员查看台账。</p>}

          {ledger && draft && (
            <DraftPanel draft={draft} reasons={ledger.reasons} busy={busy}
                        onPublish={act(() => api.publish(selected))}
                        onDiscard={act(() => api.discard(selected))} />
          )}

          {viewData && (
            <>
              <section className="headline">
                <div className="hero">
                  <span className="hero-label">当前剩余积分</span>
                  <span className="hero-value">{viewData.totals.remaining.toLocaleString()}</span>
                  <span className="hero-sub">
                    已消费 {viewData.totals.consumed.toLocaleString()} ·
                    已过期 {viewData.totals.expired.toLocaleString()}
                  </span>
                </div>
                <div className="version-bar">
                  <label>回放版本</label>
                  <select value={viewVersion ? viewVersion.version : ledger.version}
                          onChange={(e) => openVersion(
                            e.target.value === String(ledger.version) ? null : e.target.value)}>
                    {versions.map((v) => (
                      <option key={v.version} value={v.version}>
                        v{v.version} · {v.event_count} 个事件 · 剩余 {v.totals.remaining}
                      </option>
                    ))}
                  </select>
                  {viewVersion && (
                    <span className="tag st-pending">
                      正在查看历史版本 v{viewVersion.version}（对照用）
                    </span>
                  )}
                  {!viewVersion && ledger.pending_event_ids.length > 0 && (
                    <span className="tag st-pending">
                      {ledger.pending_event_ids.length} 个晚到事件待发布
                    </span>
                  )}
                </div>
              </section>

              <section className="card">
                <h2>余额曲线</h2>
                <BalanceCurve curve={viewData.curve} eventsById={eventsById} />
              </section>

              <section className="card">
                <h2>批次台账</h2>
                <BatchesTable batches={viewData.batches} />
              </section>

              <section className="card">
                <h2>事件流水</h2>
                <EventsTable events={viewData.events} reasons={viewData.reasons}
                             pendingIds={viewVersion ? [] : ledger.pending_event_ids} />
              </section>
            </>
          )}
        </main>
      </div>
    </div>
  )
}
