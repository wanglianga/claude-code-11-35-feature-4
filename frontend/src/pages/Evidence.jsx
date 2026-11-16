import { useEffect, useState } from 'react'
import { api, getUser } from '../api'
import { SOURCE_TYPE_LABEL, Ts, Weight } from '../components/Format'

const TYPES = [
  ['collection_task', '收运任务'],
  ['gate_record', '门岗记录'],
  ['weighbridge_reading', '地磅读数'],
  ['facility_receipt', '处理场签收'],
  ['rate_catalog', '费率目录'],
  ['accounting_receipt', '账务回执'],
]

export default function Evidence() {
  const [events, setEvents] = useState([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const analyst = getUser()?.role === 'analyst'
  const [form, setForm] = useState({
    source_type: 'weighbridge_reading',
    raw_text: '',
    event_time: '',
    device_id: '',
    site_id: '',
    calibration_version: '',
    weight_tons: '',
    plate: '',
  })

  function load() {
    api
      .get(`/api/evidence${filter ? `?source_type=${filter}` : ''}`)
      .then(setEvents)
      .catch((e) => setError(e.message))
  }
  useEffect(load, [filter])

  async function submit(e) {
    e.preventDefault()
    setError('')
    setNotice('')
    try {
      const payload = {}
      if (form.plate) payload.plate = form.plate
      if (form.weight_tons) payload.weight_tons = Number(form.weight_tons)
      const created = await api.post('/api/evidence', {
        source_type: form.source_type,
        raw_text: form.raw_text,
        event_time: new Date(form.event_time).toISOString(),
        device_id: form.device_id || null,
        site_id: form.site_id || null,
        calibration_version: form.calibration_version ? Number(form.calibration_version) : null,
        payload,
      })
      setNotice(`证据 #${created.id} 已入库，内容哈希 ${created.content_hash.slice(0, 16)}…`)
      setForm({ ...form, raw_text: '', weight_tons: '' })
      load()
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div>
      <h1 className="page-title">来源证据库</h1>
      <p className="page-desc">收运任务、门岗、地磅、签收、费率目录与账务回执的原文、事件时间、设备/场地标识、校准版本与哈希</p>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert ok">{notice}</div>}

      {analyst && (
        <div className="panel">
          <h2>录入证据</h2>
          <form onSubmit={submit}>
            <div className="form-row">
              <label>类型</label>
              <select value={form.source_type} onChange={(e) => setForm({ ...form, source_type: e.target.value })}>
                {TYPES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
              <label>事件时间</label>
              <input
                type="datetime-local"
                required
                value={form.event_time}
                onChange={(e) => setForm({ ...form, event_time: e.target.value })}
              />
              <label>设备标识</label>
              <input
                style={{ width: 110 }}
                value={form.device_id}
                onChange={(e) => setForm({ ...form, device_id: e.target.value })}
              />
              <label>场地</label>
              <input
                style={{ width: 100 }}
                value={form.site_id}
                onChange={(e) => setForm({ ...form, site_id: e.target.value })}
              />
            </div>
            <div className="form-row">
              <label>校准版本</label>
              <input
                style={{ width: 80 }}
                value={form.calibration_version}
                onChange={(e) => setForm({ ...form, calibration_version: e.target.value })}
              />
              <label>重量(吨)</label>
              <input
                style={{ width: 100 }}
                value={form.weight_tons}
                onChange={(e) => setForm({ ...form, weight_tons: e.target.value })}
              />
              <label>车牌</label>
              <input
                style={{ width: 110 }}
                value={form.plate}
                onChange={(e) => setForm({ ...form, plate: e.target.value })}
              />
            </div>
            <div className="form-row">
              <label>原文</label>
              <input
                style={{ flex: 1, minWidth: 320 }}
                required
                placeholder="原始单据文本，原样保存"
                value={form.raw_text}
                onChange={(e) => setForm({ ...form, raw_text: e.target.value })}
              />
              <button className="primary" type="submit">
                入库（自动计算哈希）
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="panel">
        <div className="form-row">
          <label>类型筛选</label>
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">全部</option>
            {TYPES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>类型</th>
              <th>原文</th>
              <th>事件时间</th>
              <th>设备/场地</th>
              <th>校准</th>
              <th>重量</th>
              <th>内容哈希</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td className="mono">#{e.id}</td>
                <td>{SOURCE_TYPE_LABEL[e.source_type] || e.source_type}</td>
                <td style={{ maxWidth: 260 }}>{e.raw_text}</td>
                <td>
                  <Ts value={e.event_time} />
                </td>
                <td className="mono">
                  {e.device_id || '—'} / {e.site_id || '—'}
                </td>
                <td>{e.calibration_version != null ? `v${e.calibration_version}` : '—'}</td>
                <td>
                  <Weight value={e.payload?.weight_tons} />
                </td>
                <td className="hash">{e.content_hash.slice(0, 16)}…</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
