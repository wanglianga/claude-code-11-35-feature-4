import { useState } from 'react'
import { api } from '../api'

// 数据团队录入 SLA 证据：排班任务、车辆到场、处理场签收、居民投诉。
const KINDS = [
  ['task', '排班任务'],
  ['arrival', '车辆到场'],
  ['signoff', '处理场签收'],
  ['complaint', '居民投诉'],
]

export default function SlaIntake({ contracts, onChanged }) {
  const [kind, setKind] = useState('arrival')
  const [form, setForm] = useState({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setMsg('')
    try {
      let body
      if (kind === 'task') {
        body = {
          id: form.id,
          contract_id: form.contract_id || contracts[0]?.id,
          vehicle_id: form.vehicle_id,
          address: form.address,
          scheduled_time: new Date(form.time).toISOString(),
        }
      } else if (kind === 'arrival') {
        body = {
          vehicle_id: form.vehicle_id,
          address: form.address,
          arrival_time: new Date(form.time).toISOString(),
          source_ref: form.ref,
          raw_text: form.raw || '',
        }
      } else if (kind === 'signoff') {
        body = {
          task_id: form.task_id,
          vehicle_id: form.vehicle_id,
          signoff_time: new Date(form.time).toISOString(),
          source_ref: form.ref,
          raw_text: form.raw || '',
          weight_tons: form.weight ? Number(form.weight) : null,
        }
      } else {
        body = {
          reporter: form.reporter,
          address: form.address,
          event_time: new Date(form.time).toISOString(),
          category: form.category || 'other',
          description: form.raw || '',
          raw_text: form.raw || '',
        }
      }
      const r = await api.post(`/api/sla/${kind === 'task' ? 'tasks' : kind === 'arrival' ? 'arrivals' : kind === 'signoff' ? 'signoffs' : 'complaints'}`, body)
      setMsg(`已录入 #${r.id ?? form.id}（内容哈希已留存）${r.task_id ? `，匹配车次 ${r.task_id}` : ''}${r.lock_adjustment_id ? `，锁定调整单 #${r.lock_adjustment_id}` : ''}`)
      setForm({})
      onChanged?.()
    } catch (err) {
      setMsg(`录入失败：${err.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="panel">
      <summary>
        <b>数据团队录入 SLA 证据</b>
        <span className="tip">排班 / 到场 / 签收 / 投诉，写入即计算内容哈希并自动匹配受影响车次</span>
      </summary>
      <form onSubmit={submit} style={{ marginTop: 12 }}>
        <div className="form-row">
          <label>证据类型</label>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <label>事件时间</label>
          <input type="datetime-local" required value={form.time || ''} onChange={set('time')} />
        </div>

        {kind === 'task' && (
          <div className="form-row">
            <input placeholder="任务号 ST-..." required value={form.id || ''} onChange={set('id')} />
            <select value={form.contract_id || contracts[0]?.id || ''} onChange={set('contract_id')}>
              {contracts.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id}
                </option>
              ))}
            </select>
            <input placeholder="车牌" required value={form.vehicle_id || ''} onChange={set('vehicle_id')} />
            <input placeholder="地址" required value={form.address || ''} onChange={set('address')} />
          </div>
        )}

        {kind === 'arrival' && (
          <div className="form-row">
            <input placeholder="车牌" required value={form.vehicle_id || ''} onChange={set('vehicle_id')} />
            <input placeholder="地址（与排班一致）" required value={form.address || ''} onChange={set('address')} />
            <input placeholder="到场流水号" value={form.ref || ''} onChange={set('ref')} />
          </div>
        )}

        {kind === 'signoff' && (
          <div className="form-row">
            <input placeholder="任务号 ST-..." required value={form.task_id || ''} onChange={set('task_id')} />
            <input placeholder="车牌" required value={form.vehicle_id || ''} onChange={set('vehicle_id')} />
            <input placeholder="签收单号" value={form.ref || ''} onChange={set('ref')} />
            <input placeholder="重量(吨)" value={form.weight || ''} onChange={set('weight')} />
          </div>
        )}

        {kind === 'complaint' && (
          <div className="form-row">
            <input placeholder="报告人" required value={form.reporter || ''} onChange={set('reporter')} />
            <input placeholder="地址" required value={form.address || ''} onChange={set('address')} />
            <select value={form.category || 'other'} onChange={set('category')}>
              <option value="missed_collection">漏收</option>
              <option value="spillage">污水/散落</option>
              <option value="odor">异味</option>
              <option value="other">其他</option>
            </select>
          </div>
        )}

        <div className="form-row">
          <input style={{ flex: 1, minWidth: 300 }} placeholder="原文/说明（原样保存）" value={form.raw || ''} onChange={set('raw')} />
          <button className="primary" disabled={busy} type="submit">
            录入
          </button>
        </div>
        {msg && <div className="alert ok">{msg}</div>}
      </form>
    </details>
  )
}
