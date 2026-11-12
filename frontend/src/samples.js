// 三个演示样例：跨到期时刻消费、跨批次部分退款、晚到事件。
// 时间均相对当前时刻生成，便于在页面上直接看到过期效果与新旧版本对照。

const iso = (d) => d.toISOString()
const ago = (h) => iso(new Date(Date.now() - h * 3600e3))
const later = (h) => iso(new Date(Date.now() + h * 3600e3))

const claim = (event_id, member_id, t, amount, expires_at, seq = 0) =>
  ({ event_id, member_id, type: 'claim', occurred_at: t, seq, amount, expires_at })
const consume = (event_id, member_id, t, amount, seq = 0) =>
  ({ event_id, member_id, type: 'consume', occurred_at: t, seq, amount })
const refund = (event_id, member_id, t, amount, ref_event_id, seq = 0) =>
  ({ event_id, member_id, type: 'refund', occurred_at: t, seq, amount, ref_event_id })

export const SAMPLES = [
  {
    key: 'expiry-consume',
    name: '样例一 · 跨到期时刻消费',
    desc: '消费先扣最早到期批次；到期时刻起不能用；余额不足整笔不扣。',
    steps: [
      [
        claim('ex-c1', 'demo-expiry', ago(3), 100, ago(1)),
        claim('ex-c2', 'demo-expiry', ago(3), 100, later(9)),
        consume('ex-s1', 'demo-expiry', ago(2.5), 60),   // 全部扣自最早到期的 ex-c1
        consume('ex-s2', 'demo-expiry', ago(0.5), 30),   // ex-c1 已过期，只能扣 ex-c2
        consume('ex-s3', 'demo-expiry', ago(0.3), 200),  // 余额不足，整笔不扣
      ],
    ],
  },
  {
    key: 'partial-refund',
    name: '样例二 · 跨批次部分退款',
    desc: '退款按原扣减顺序逐批恢复；到期批次恢复记为过期；累计退款不能超过原消费。',
    steps: [
      [
        claim('rf-c1', 'demo-refund', ago(6), 50, ago(1)),
        claim('rf-c2', 'demo-refund', ago(6), 100, later(5)),
        claim('rf-c3', 'demo-refund', ago(5.5), 100, later(19)),
        consume('rf-s1', 'demo-refund', ago(5), 180),    // 50 rf-c1 + 100 rf-c2 + 30 rf-c3
        refund('rf-r1', 'demo-refund', ago(2), 80, 'rf-s1'),   // rf-c1 已过期 → 50 记为过期
        refund('rf-r2', 'demo-refund', ago(1), 60, 'rf-s1'),   // 继续恢复 rf-c2 剩余额度
        refund('rf-r3', 'demo-refund', ago(0.5), 50, 'rf-s1'), // 累计 190 > 180，整笔失败
      ],
    ],
  },
  {
    key: 'late-event',
    name: '样例三 · 晚到事件',
    desc: '晚到消费先生成重算草稿：原有效消费余额不足、退款引用失效，提示冲突，人工发布或丢弃。',
    steps: [
      [
        claim('lt-c1', 'demo-late', ago(4), 100, later(20)),
        consume('lt-s1', 'demo-late', ago(3), 40),
        refund('lt-r1', 'demo-late', ago(2), 10, 'lt-s1'),
      ],
      [
        // 晚到：发生在 lt-s1 之前，回放后 lt-s1 余额不足、lt-r1 引用失效
        consume('lt-s0', 'demo-late', ago(3.5), 80),
      ],
    ],
  },
]

export const EXAMPLE_PAYLOAD = JSON.stringify(
  {
    events: [
      { event_id: 'm-c1', member_id: 'm1001', type: 'claim', occurred_at: ago(2), seq: 1, amount: 100, expires_at: later(24) },
      { event_id: 'm-s1', member_id: 'm1001', type: 'consume', occurred_at: ago(1), seq: 1, amount: 30 },
      { event_id: 'm-r1', member_id: 'm1001', type: 'refund', occurred_at: ago(0.5), seq: 1, amount: 10, ref_event_id: 'm-s1' },
    ],
  },
  null,
  2,
)
