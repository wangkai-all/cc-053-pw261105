# 会员积分台账

会员余额从哪来、花到哪去、退款后为什么没变多，都能从台账查清。
导入领取 / 消费 / 退款事件，按 **会员 + 发生时间 + 稳定序号** 回放，
页面展示各批次的剩余、已消费、过期数量与余额曲线。

技术栈：FastAPI + SQLite + Python（后端）、React + Vite（前端，手写 SVG 阶梯曲线）。

## 运行

```bash
# 后端（首次先建虚拟环境）
python3 -m venv .venv
.venv/bin/pip install fastapi uvicorn pytest httpx

# 前端（构建产物由后端托管）
cd frontend && npm install && npm run build && cd ..

# 启动：http://localhost:8000 （API 在 /api 下）
cd backend && ../.venv/bin/uvicorn app.main:app --port 8000
```

开发前端时可用 `cd frontend && npm run dev`（Vite 已配置 /api 代理到 8000）。

## 测试

```bash
cd backend && ../.venv/bin/python -m pytest tests/ -q   # 20 个用例
```

## 业务规则

**回放**：同一会员的事件按 `(occurred_at, seq, event_id)` 排序重放，
台账状态（批次剩余/已消费/过期、事件结果、余额曲线）完全由事件流决定，
每次发布生成一个可对照的回放版本。

**消费**：先扣最早到期的一批；到期时间相同再按领取时间与批次编号排。
到期时刻起不能使用（`occurred_at >= expires_at` 即不可扣）。
可用余额不足则整笔消费不扣，记为失败。

**幂等**：`event_id` 相同且内容一致只处理一次（返回 duplicate）；
内容改变报冲突（conflict），不覆盖已处理事件。

**退款**：必须引用原消费。部分退款按原扣减顺序逐批恢复，
每批最多恢复「当初实际扣掉且尚未退回」的数量。
原批次在退款时已经到期的，恢复部分直接记为过期，不重新获得有效期。
多次退款合计不能超过原消费金额；失败的消费不能退款。

**晚到事件**：排序位置落在已应用事件之前（或已有草稿待处理）的事件不直接发布，
先生成 **重算草稿**（含全部待发布事件的新回放）。若原有效消费因此余额不足，
或退款引用变得无效，草稿中提示冲突，由用户在页面上选择
**发布草稿**（接受新回放，生成新版本）或 **丢弃草稿**（撤回晚到事件）。

## 页面与样例

页面右上「导入事件 / 样例」提供三个内置样例（时间相对当前时刻生成）：

1. **跨到期时刻消费**：两批积分先后到期，观察先扣早到期批次、
   到期后不可再扣、余额不足整笔失败。
2. **跨批次部分退款**：一笔消费扣了三批，两次部分退款按原顺序恢复
   （其中一批已到期，恢复记为过期），第三次退款因累计超额失败。
3. **晚到事件**：先建立「领取 → 消费 → 退款」，再补发一笔更早的消费，
   生成带冲突的重算草稿，可对照新旧回放版本后决定发布或丢弃。

「回放版本」下拉可查看任一历史发布版本的台账与曲线，用于新旧对照。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/events/import` | 批量导入事件，逐条返回 applied/pending/duplicate/conflict/error |
| GET | `/api/members` | 会员列表与当前合计 |
| GET | `/api/members/{id}/ledger` | 已发布台账：批次、事件流水、余额曲线 |
| GET | `/api/members/{id}/draft` | 重算草稿：冲突列表 + 与发布版对照 |
| POST | `/api/members/{id}/draft/publish` | 发布草稿 |
| POST | `/api/members/{id}/draft/discard` | 丢弃草稿 |
| GET | `/api/members/{id}/versions` | 回放版本列表 |
| GET | `/api/members/{id}/versions/{n}` | 指定版本快照 |
| POST | `/api/demo/reset` | 清空全部数据 |

事件格式：

```json
{
  "event_id": "m-c1", "member_id": "m1001", "type": "claim",
  "occurred_at": "2026-11-13T08:00:00Z", "seq": 1, "amount": 100,
  "expires_at": "2026-11-14T08:00:00Z"
}
```

- `claim` 需要 `expires_at`；`refund` 需要 `ref_event_id`（原消费事件编号）；
  `amount` 为正整数；`seq` 为稳定序号（同一时间内的回放次序，缺省 0）。

## 目录

```
backend/app/engine.py   # 纯函数回放引擎（规则全部在此）
backend/app/store.py    # 导入、幂等、晚到判定、草稿与版本
backend/app/db.py       # SQLite schema
backend/app/main.py     # FastAPI 路由 + 前端静态托管
backend/tests/          # 引擎规则与 API 集成测试
frontend/src/           # React 页面（台账、曲线、草稿对照、导入）
```
