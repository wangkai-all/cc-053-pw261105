import React from 'react'

const TYPE_LABEL = { claim: '领取', consume: '消费', refund: '退款' }
const TO_LABEL = { remaining: '恢复可用', expired: '记为过期', consumed: '扣减' }

export const fmtTime = (t) => {
  if (!t) return '—'
  const d = new Date(t)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function batchStatus(b) {
  const expired = new Date(b.expires_at).getTime() <= Date.now()
  if (expired) return { label: '已到期', cls: 'st-expired' }
  if (b.remaining === 0) return { label: '已用完', cls: 'st-used' }
  return { label: '有效', cls: 'st-active' }
}

export function lineText(e) {
  if (!e.lines || e.lines.length === 0) return '—'
  if (e.type === 'claim') return `批次 ${e.lines[0].batch_id} +${e.amount}`
  if (e.type === 'consume') {
    return e.lines.map((l) => {
      const back = l.refunded > 0 ? `（已退 ${l.refunded}）` : ''
      return `${l.batch_id} −${l.amount}${back}`
    }).join('，')
  }
  return e.lines.map((l) =>
    `${l.batch_id} +${l.amount}${l.to === 'expired' ? '（记为过期）' : ''}`
  ).join('，')
}

export function BatchesTable({ batches }) {
  return (
    <table>
      <thead>
        <tr>
          <th>批次</th><th>领取时间</th><th>到期时间</th>
          <th className="num">总额</th><th className="num">剩余</th>
          <th className="num">已消费</th><th className="num">已过期</th><th>状态</th>
        </tr>
      </thead>
      <tbody>
        {batches.map((b) => {
          const st = batchStatus(b)
          return (
            <tr key={b.batch_id}>
              <td className="mono">{b.batch_id}</td>
              <td>{fmtTime(b.claimed_at)}</td>
              <td>{fmtTime(b.expires_at)}</td>
              <td className="num">{b.amount.toLocaleString()}</td>
              <td className="num strong">{b.remaining.toLocaleString()}</td>
              <td className="num">{b.consumed.toLocaleString()}</td>
              <td className="num">{b.expired.toLocaleString()}</td>
              <td><span className={`tag ${st.cls}`}>{st.label}</span></td>
            </tr>
          )
        })}
        {batches.length === 0 && (
          <tr><td colSpan={8} className="empty">暂无批次</td></tr>
        )}
      </tbody>
    </table>
  )
}

export function EventsTable({ events, reasons, pendingIds = [] }) {
  const pending = new Set(pendingIds)
  return (
    <table>
      <thead>
        <tr>
          <th>发生时间</th><th>事件编号</th><th>类型</th>
          <th className="num">金额</th><th>结果</th><th>批次明细</th><th>备注</th>
        </tr>
      </thead>
      <tbody>
        {events.map((e) => (
          <tr key={e.event_id} className={pending.has(e.event_id) ? 'row-pending' : ''}>
            <td>{fmtTime(e.occurred_at)}<span className="seq">#{e.seq}</span></td>
            <td className="mono">{e.event_id}{pending.has(e.event_id) && <span className="tag st-pending">待发布</span>}</td>
            <td>{TYPE_LABEL[e.type]}</td>
            <td className="num">{e.amount.toLocaleString()}</td>
            <td>
              {e.result === 'success'
                ? <span className="tag st-ok">✓ 成功</span>
                : <span className="tag st-fail">✗ 失败</span>}
            </td>
            <td className="lines">{lineText(e)}</td>
            <td className="note">
              {e.reason ? (reasons?.[e.reason] || e.reason) : ''}
              {e.type === 'consume' && e.refunded_total > 0 && `累计已退 ${e.refunded_total}`}
              {e.type === 'refund' && e.ref_event_id && `原消费 ${e.ref_event_id}`}
            </td>
          </tr>
        ))}
        {events.length === 0 && (
          <tr><td colSpan={7} className="empty">暂无事件</td></tr>
        )}
      </tbody>
    </table>
  )
}
