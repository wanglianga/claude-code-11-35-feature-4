import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getUser } from '../api'
import Badge from '../components/Badge'
import SlaIntake from '../components/SlaIntake'
import { COMPLAINT_CATEGORY_LABEL, Ts, VIOLATION_LABEL } from '../components/Format'

export default function Complaints() {
  const user = getUser()
  const analyst = user?.role === 'analyst'
  const supervisor = user?.role === 'supervisor'
  const [complaints, setComplaints] = useState([])
  const [groups, setGroups] = useState([])
  const [appeals, setAppeals] = useState([])
  const [tasks, setTasks] = useState([])
  const [contracts, setContracts] = useState([])
  const [tab, setTab] = useState('merges')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.get('/api/sla/complaints').then(setComplaints).catch((e) => setError(e.message))
    api.get('/api/sla/merge-groups').then(setGroups).catch(() => {})
    api.get('/api/sla/appeals').then(setAppeals).catch(() => {})
    api.get('/api/sla/tasks?month=2026-11').then(setTasks).catch(() => {})
    api.get('/api/contracts').then(setContracts).catch(() => {})
  }, [])
  useEffect(load, [load])

  async function call(path, body, label, method = 'POST') {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await api.post(path, body)
      setNotice(`${label}完成`)
      load()
    } catch (e) {
      setError(`${label}失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h1 className="page-title">投诉合并、核实与申辩裁定</h1>
      <p className="page-desc">
        重复投诉按地址、时间与事件关系合并，多人报告同一问题不重复计罚；未核投诉只列风险；申辩与主管裁定并列保存
      </p>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert ok">{notice}</div>}

      {analyst && <SlaIntake contracts={contracts} onChanged={load} />}

      <div className="tabs">
        <button className={tab === 'merges' ? 'active' : ''} onClick={() => setTab('merges')}>
          合并候选与投诉
        </button>
        <button className={tab === 'appeals' ? 'active' : ''} onClick={() => setTab('appeals')}>
          承运商申辩与裁定
        </button>
      </div>

      {tab === 'merges' && (
        <>
          <div className="panel">
            <div className="actions">
              {analyst && (
                <button disabled={busy} onClick={() => call('complaints/detect-merges', {}, '扫描合并候选')}>
                  扫描合并候选（地址+时间窗）
                </button>
              )}
            </div>
            <h2 style={{ marginTop: 12 }}>合并组（同组确认后整组只计一次罚分）</h2>
            {groups.length === 0 ? (
              <p className="muted">暂无合并组</p>
            ) : (
              groups.map((g) => (
                <div key={g.id} className="panel" style={{ marginBottom: 10 }}>
                  <div className="kv">
                    <dt>合并组 #{g.id}</dt>
                    <dd>
                      <Badge value={g.status} /> · {g.address} · 首报 <Ts value={g.event_time} /> · 关系 {g.relation}
                    </dd>
                    <dt>系统依据</dt>
                    <dd className="muted">{g.reason}</dd>
                  </div>
                  <table>
                    <thead>
                      <tr>
                        <th>投诉#</th>
                        <th>报告人</th>
                        <th>事件时间</th>
                        <th>类别</th>
                        <th>状态</th>
                        <th>关联车次</th>
                      </tr>
                    </thead>
                    <tbody>
                      {g.members.map((c) => (
                        <tr key={c.id}>
                          <td>#{c.id}</td>
                          <td>{c.reporter}</td>
                          <td>
                            <Ts value={c.event_time} />
                          </td>
                          <td>{COMPLAINT_CATEGORY_LABEL[c.category] || c.category}</td>
                          <td>
                            <Badge value={c.status} />
                          </td>
                          <td>{c.task_id ? <Link to={`/sla/tasks/${c.task_id}`}>{c.task_id}</Link> : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {g.status === 'candidate' && supervisor && (
                    <div className="actions" style={{ marginTop: 8 }}>
                      <button
                        className="small primary"
                        disabled={busy}
                        onClick={() =>
                          call(
                            `merge-groups/${g.id}/decision`,
                            { confirm: true, reason: window.prompt('确认同一事件的依据', '现场核查为同一事件') || '' },
                            '确认合并',
                          )
                        }
                      >
                        确认同一事件
                      </button>
                      <button
                        className="small"
                        disabled={busy}
                        onClick={() =>
                          call(
                            `merge-groups/${g.id}/decision`,
                            { confirm: false, reason: window.prompt('解除合并的依据', '并非同一事件') || '' },
                            '解除合并',
                          )
                        }
                      >
                        并非同一事件（解除）
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>

          <div className="panel">
            <h2>
              全部投诉
              <span className="tip">未核实只列风险不扣分；核实并关联车次后才进入评分</span>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>报告人</th>
                  <th>地址</th>
                  <th>事件时间</th>
                  <th>类别</th>
                  <th>状态</th>
                  <th>合并组</th>
                  <th>关联车次</th>
                  {supervisor && <th>主管操作</th>}
                </tr>
              </thead>
              <tbody>
                {complaints.map((c) => (
                  <tr key={c.id}>
                    <td>#{c.id}</td>
                    <td>{c.reporter}</td>
                    <td>{c.address}</td>
                    <td>
                      <Ts value={c.event_time} />
                    </td>
                    <td>{COMPLAINT_CATEGORY_LABEL[c.category] || c.category}</td>
                    <td>
                      <Badge value={c.status} />
                    </td>
                    <td>{c.merge_group_id ? `#${c.merge_group_id}` : '—'}</td>
                    <td>{c.task_id ? <Link to={`/sla/tasks/${c.task_id}`}>{c.task_id}</Link> : '—'}</td>
                    {supervisor && (
                      <td>
                        {c.status !== 'dismissed' && (
                          <div className="actions">
                            <button
                              className="small"
                              disabled={busy}
                              onClick={() => {
                                const tid = window.prompt('核实成立：关联受影响车次（任务号）', c.task_id || '')
                                if (tid !== null) {
                                  call(
                                    `complaints/${c.id}/verify`,
                                    { task_id: tid || null, note: window.prompt('核实说明', '主管核实成立') || '', dismiss: false },
                                    '投诉核实',
                                  )
                                }
                              }}
                            >
                              核实
                            </button>
                            <button
                              className="small"
                              disabled={busy}
                              onClick={() =>
                                call(
                                  `complaints/${c.id}/verify`,
                                  { task_id: null, note: window.prompt('驳回说明', '投诉不成立') || '', dismiss: true },
                                  '驳回投诉',
                                )
                              }
                            >
                              驳回
                            </button>
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ marginTop: 8 }}>
              可选车次（2026-11）：{tasks.map((t) => t.id).join('、') || '加载中…'}
            </p>
          </div>
        </>
      )}

      {tab === 'appeals' && (
        <div className="panel">
          <h2>
            承运商申辩与主管裁定
            <span className="tip">申辩只改变对应任务的裁定状态；与评分发布并发时主管需重看影响车次</span>
          </h2>
          {analyst && (
            <form
              className="form-row"
              onSubmit={(e) => {
                e.preventDefault()
                const fd = new FormData(e.target)
                call(
                  'appeals',
                  {
                    task_id: fd.get('task_id'),
                    violation_type: fd.get('violation_type'),
                    reason: fd.get('reason'),
                    carrier_ref: fd.get('carrier_ref') || '',
                  },
                  '登记承运商申辩',
                )
                e.target.reset()
              }}
            >
              <input name="task_id" placeholder="车次 ST-..." required style={{ width: 150 }} />
              <select name="violation_type" defaultValue="missed_collection">
                <option value="missed_collection">漏收</option>
                <option value="late_arrival">到场超时</option>
                <option value="late_receipt">迟到签收</option>
                <option value="valid_complaint">核实投诉</option>
              </select>
              <input name="carrier_ref" placeholder="申辩单号" style={{ width: 130 }} />
              <input name="reason" placeholder="申辩理由" required style={{ flex: 1, minWidth: 200 }} />
              <button className="primary" disabled={busy} type="submit">
                登记申辩
              </button>
            </form>
          )}
          {appeals.length === 0 ? (
            <p className="muted">暂无申辩</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>申辩#</th>
                  <th>车次</th>
                  <th>违约类型</th>
                  <th>申辩理由</th>
                  <th>状态/裁定</th>
                  <th>主管操作</th>
                </tr>
              </thead>
              <tbody>
                {appeals.map((a) => (
                  <tr key={a.id}>
                    <td>
                      {a.id}
                      <div className="muted mono">{a.carrier_ref}</div>
                    </td>
                    <td>
                      <Link to={`/sla/tasks/${a.task_id}`}>{a.task_id}</Link>
                    </td>
                    <td>{VIOLATION_LABEL[a.violation_type] || a.violation_type}</td>
                    <td style={{ maxWidth: 260 }}>{a.reason}</td>
                    <td>
                      <Badge value={a.status} />
                      {a.ruling && <div className="muted" style={{ fontSize: 11 }}>{a.ruling.ruling_note}</div>}
                    </td>
                    <td>
                      {a.status === 'pending' && supervisor && (
                        <div className="actions">
                          <button
                            className="small"
                            disabled={busy}
                            onClick={() =>
                              call(
                                `appeals/${a.id}/rule`,
                                { outcome: 'overturned', ruling_note: window.prompt('采纳申辩的裁定说明', '证据成立，撤销违约') || '' },
                                '采纳申辩',
                              )
                            }
                          >
                            采纳（撤销）
                          </button>
                          <button
                            className="small"
                            disabled={busy}
                            onClick={() =>
                              call(
                                `appeals/${a.id}/rule`,
                                { outcome: 'upheld', ruling_note: window.prompt('驳回申辩的裁定说明', '理由不成立，维持违约') || '' },
                                '驳回申辩',
                              )
                            }
                          >
                            驳回（维持）
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
