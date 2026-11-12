import React, { useMemo, useState } from 'react'

// 余额曲线：单序列阶梯折线（step-after），事件时刻余额突变。
// 视觉规范：2px 折线、r4 圆点带 2px 底色环、10% 面积淡填充、
// 发丝网格线、十字准线 + 悬浮提示；文本一律用墨色，不穿序列色。

const KIND_LABEL = {
  claim: '领取', consume: '消费', refund: '退款', expire: '到期', now: '当前',
}

const W = 760
const H = 260
const M = { top: 14, right: 18, bottom: 30, left: 48 }

function niceTicks(max, count = 4) {
  if (max <= 0) return [0, 1]
  const rough = max / count
  const mag = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough) || 10 * mag
  const ticks = []
  for (let v = 0; v <= max + step * 0.5; v += step) ticks.push(Math.round(v))
  return ticks
}

const fmtTime = (t, spanH) => {
  const d = new Date(t)
  const p = (n) => String(n).padStart(2, '0')
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`
  return spanH > 48 ? `${p(d.getMonth() + 1)}-${p(d.getDate())} ${hm}` : hm
}
const fmtFull = (t) => {
  const d = new Date(t)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export default function BalanceCurve({ curve, eventsById }) {
  const [hover, setHover] = useState(null)

  const model = useMemo(() => {
    if (!curve || curve.length === 0) return null
    const pts = curve.map((c) => ({ ...c, x: new Date(c.t).getTime() }))
    let x0 = pts[0].x
    let x1 = pts[pts.length - 1].x
    if (x1 - x0 < 60000) { x0 -= 3600e3; x1 += 3600e3 }
    const maxY = Math.max(0, ...pts.map((p) => p.balance))
    const ticks = niceTicks(maxY)
    const yTop = ticks[ticks.length - 1]
    const sx = (x) => M.left + ((x - x0) / (x1 - x0)) * (W - M.left - M.right)
    const sy = (y) => M.top + (1 - y / yTop) * (H - M.top - M.bottom)
    // step-after 路径：先水平走到下一时刻，再垂直落到新余额
    let d = `M ${sx(pts[0].x)} ${sy(pts[0].balance)}`
    for (let i = 1; i < pts.length; i++) {
      d += ` H ${sx(pts[i].x)} V ${sy(pts[i].balance)}`
    }
    const area = `${d} L ${sx(pts[pts.length - 1].x)} ${sy(0)} L ${sx(pts[0].x)} ${sy(0)} Z`
    // x 轴刻度：均匀取 5 个时刻
    const xticks = []
    const n = Math.min(5, pts.length)
    for (let i = 0; i < n; i++) {
      const p = pts[Math.round((i * (pts.length - 1)) / (n - 1 || 1))]
      xticks.push({ x: sx(p.x), label: fmtTime(p.x, (x1 - x0) / 3600e3) })
    }
    return { pts, sx, sy, ticks, d, area, xticks, x0, x1 }
  }, [curve])

  if (!model) return <div className="curve-empty">暂无曲线数据</div>
  const { pts, sx, sy, ticks, d, area, xticks, x0, x1 } = model

  const onMove = (e) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const px = ((e.clientX - rect.left) / rect.width) * W
    const t = x0 + ((px - M.left) / (W - M.left - M.right)) * (x1 - x0)
    let best = 0
    for (let i = 0; i < pts.length; i++) {
      if (Math.abs(pts[i].x - t) < Math.abs(pts[best].x - t)) best = i
    }
    setHover(best)
  }

  const hp = hover != null ? pts[hover] : null
  const hEvent = hp?.event_id ? eventsById?.[hp.event_id] : null

  return (
    <div className="curve-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="curve" role="img"
           aria-label="余额曲线" onPointerLeave={() => setHover(null)}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={M.left} x2={W - M.right} y1={sy(v)} y2={sy(v)}
                  className="gridline" />
            <text x={M.left - 8} y={sy(v) + 4} textAnchor="end" className="tick">
              {v.toLocaleString()}
            </text>
          </g>
        ))}
        <line x1={M.left} x2={W - M.right} y1={sy(0)} y2={sy(0)} className="baseline" />
        {xticks.map((t, i) => (
          <text key={i} x={t.x} y={H - 8} textAnchor="middle" className="tick">
            {t.label}
          </text>
        ))}
        <path d={area} className="curve-area" />
        <path d={d} className="curve-line" />
        {pts.map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.balance)} r={4}
                  className={p.kind === 'expire' || p.kind === 'now'
                    ? 'curve-dot hollow' : 'curve-dot'} />
        ))}
        {hp && (
          <line x1={sx(hp.x)} x2={sx(hp.x)} y1={M.top} y2={H - M.bottom}
                className="crosshair" />
        )}
        <rect x={M.left} y={M.top} width={W - M.left - M.right}
              height={H - M.top - M.bottom} fill="transparent"
              onPointerMove={onMove} />
      </svg>
      {hp && (
        <div className="tooltip" style={{
          left: `${(sx(hp.x) / W) * 100}%`,
          top: `${(sy(hp.balance) / H) * 100}%`,
        }}>
          <strong>{hp.balance.toLocaleString()} 分</strong>
          <span>{KIND_LABEL[hp.kind] || hp.kind}{hEvent ? ` · ${hp.event_id}` : ''}</span>
          <span className="tooltip-time">{fmtFull(hp.x)}</span>
        </div>
      )}
    </div>
  )
}
