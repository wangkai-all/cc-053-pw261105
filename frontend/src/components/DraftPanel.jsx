import React from 'react'

// 重算草稿：冲突提示 + 新旧回放版本对照 + 发布/丢弃
const KIND_LABEL = {
  consume_now_insufficient: '原有效消费余额不足',
  refund_now_invalid: '退款引用失效',
}
const TYPE_LABEL = { claim: '领取', consume: '消费', refund: '退款' }

export default function DraftPanel({ draft, reasons, onPublish, onDiscard, busy }) {
  const baseEvents = new Map((draft.base?.events || []).map((e) => [e.event_id, e]))
  const rows = draft.events.map((e) => {
    const before = baseEvents.get(e.event_id)
    return {
      id: e.event_id, type: e.type, amount: e.amount,
      before: before ? before.result : null,
      after: e.result,
      changed: !before || before.result !== e.result,
      reason: e.reason,
    }
  }).filter((r) => r.changed || draft.pending_event_ids.includes(r.id))

  return (
    <section className="draft-panel">
      <div className="draft-head">
        <h2>重算草稿 v{draft.draft_version}（未发布）</h2>
        <span className="draft-sub">
          晚到事件 {draft.pending_event_ids.join('、')} 触发了重新回放，
          对照当前发布版本 v{draft.base?.version ?? '—'} 确认后再发布。
        </span>
      </div>

      {draft.conflicts.length > 0 && (
        <div className="conflicts">
          <h3>⚠ {draft.conflicts.length} 处冲突</h3>
          <ul>
            {draft.conflicts.map((c) => (
              <li key={c.event_id}>
                <strong>{c.event_id}</strong>（{TYPE_LABEL[c.type]} {c.amount}）：
                {KIND_LABEL[c.kind]}
                {c.reason && <em> — {reasons?.[c.reason] || c.reason}</em>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="compare">
        <div className="compare-col">
          <h3>当前发布 v{draft.base?.version ?? '—'}</h3>
          <dl>
            <dt>剩余</dt><dd>{draft.base?.totals.remaining.toLocaleString() ?? '—'}</dd>
            <dt>已消费</dt><dd>{draft.base?.totals.consumed.toLocaleString() ?? '—'}</dd>
            <dt>已过期</dt><dd>{draft.base?.totals.expired.toLocaleString() ?? '—'}</dd>
          </dl>
        </div>
        <div className="compare-arrow">→</div>
        <div className="compare-col draft">
          <h3>重算草稿 v{draft.draft_version}</h3>
          <dl>
            <dt>剩余</dt><dd>{draft.totals.remaining.toLocaleString()}</dd>
            <dt>已消费</dt><dd>{draft.totals.consumed.toLocaleString()}</dd>
            <dt>已过期</dt><dd>{draft.totals.expired.toLocaleString()}</dd>
          </dl>
        </div>
      </div>

      {rows.length > 0 && (
        <table className="diff-table">
          <thead>
            <tr><th>事件</th><th>类型</th><th className="num">金额</th><th>发布版结果</th><th>草稿结果</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={r.changed && r.before ? 'row-conflict' : ''}>
                <td className="mono">{r.id}{r.before === null && <span className="tag st-pending">新增</span>}</td>
                <td>{TYPE_LABEL[r.type]}</td>
                <td className="num">{r.amount.toLocaleString()}</td>
                <td>{r.before === null ? '—' : r.before === 'success' ? '成功' : '失败'}</td>
                <td>{r.after === 'success' ? '成功' : `失败${r.reason ? `（${reasons?.[r.reason] || r.reason}）` : ''}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="draft-actions">
        <button className="primary" disabled={busy} onClick={onPublish}>
          发布草稿（接受新回放）
        </button>
        <button className="ghost" disabled={busy} onClick={onDiscard}>
          丢弃草稿（撤回晚到事件）
        </button>
      </div>
    </section>
  )
}
