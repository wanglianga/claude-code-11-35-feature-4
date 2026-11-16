import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import Badge from '../components/Badge'
import { Json, Ts, VIOLATION_LABEL } from '../components/Format'

export default function TaskDrilldown() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api.get(`/api/sla/tasks/${id}`).then(setData).catch((e) => setError(e.message))
  }, [id])
  useEffect(load, [load])

  if (!data) return <div>{error ? <div className="alert error">{error}</div> : '加载中…'}</div>

  const { task, contract_clause: clause, arrivals, signoffs, complaints, appeals, compliance_snapshot: snap } = data

  return (
    <div>
      <h1 className="page-title">受影响车次 {task.id}</h1>
      <p className="page-desc">
        评分下钻：合同条款 → 任务证据（到场/签收/投诉）→ 申辩与主管裁决，逐项可回查
      </p>
      {error && <div className="alert error">{error}</div>}

      <div className="cards">
        <div className="card">
          <div className="k">车辆 / 地址</div>
          <div className="v" style={{ fontSize: 15 }}>
            {task.vehicle_id}
            <small> {task.address}</small>
          </div>
        </div>
        <div className="card">
          <div className="k">承诺时间</div>
          <div className="v" style={{ fontSize: 15 }}>
            <Ts value={task.scheduled_time} />
          </div>
        </div>
        <div className="card">
          <div className="k">评分资格</div>
          <div className="v" style={{ fontSize: 15 }}>
            <Badge value={task.status} />
          </div>
        </div>
        <div className="card">
          <div className="k">本车次扣罚</div>
          <div className="v" style={{ fontSize: 18 }}>{snap.deduction} 分</div>
        </div>
      </div>

      <div className="panel">
        <h2>
          ① 合同条款（{clause.contract_id}）
          <span className="tip">{clause.confirmed ? '已确认合同，任务进入评分' : '合同未确认，任务排除'}</span>
        </h2>
        <div className="kv">
          <dt>到场容差</dt>
          <dd>{clause.sla_rules.on_time_arrival_minutes} 分钟</dd>
          <dt>签收时限</dt>
          <dd>承诺后 {clause.sla_rules.receipt_due_hours} 小时</dd>
          <dt>投诉合并窗口</dt>
          <dd>{clause.sla_rules.complaint_merge_window_minutes} 分钟 / {clause.sla_rules.complaint_radius_meters} 米</dd>
          <dt>罚则（分）</dt>
          <dd className="mono">{JSON.stringify(clause.sla_rules.penalties)}</dd>
        </div>
      </div>

      <div className="panel">
        <h2>② 合规判定快照</h2>
        <div className="kv">
          <dt>证据完整</dt>
          <dd>{snap.evidence_complete ? '到场+签收齐全' : '存在证据缺口'}</dd>
          <dt>到场</dt>
          <dd>{snap.arrival ? <Ts value={snap.arrival.arrival_time} /> : '无到场证据（漏收）'}</dd>
          <dt>签收</dt>
          <dd>
            {snap.signoff ? (
              <>
                <Ts value={snap.signoff.signoff_time} />
                {snap.signoff.delay_hours > 0 && <Badge value="pending" />}
              </>
            ) : (
              '无签收证据'
            )}
          </dd>
        </div>
        <h2 style={{ marginTop: 12 }}>认定违约</h2>
        {snap.violations.length === 0 ? (
          <p className="muted">无</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>类型</th>
                <th>条款依据</th>
                <th>扣罚</th>
                <th>证据</th>
              </tr>
            </thead>
            <tbody>
              {snap.violations.map((v, i) => (
                <tr key={i}>
                  <td>{VIOLATION_LABEL[v.type] || v.type}</td>
                  <td>{v.clause}</td>
                  <td>{v.deduction} 分</td>
                  <td className="hash">{v.evidence ? JSON.stringify(v.evidence) : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {snap.removed_violations?.length > 0 && (
          <>
            <h2 style={{ marginTop: 12 }}>申辩撤销的违约（不计罚）</h2>
            <table>
              <thead>
                <tr>
                  <th>类型</th>
                  <th>处理</th>
                </tr>
              </thead>
              <tbody>
                {snap.removed_violations.map((v, i) => (
                  <tr key={i}>
                    <td>{VIOLATION_LABEL[v.type] || v.type}</td>
                    <td className="muted">{v.removed_by}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      <div className="panel">
        <h2>③ 车辆到场证据</h2>
        {arrivals.length === 0 ? (
          <p className="muted">无到场证据</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>事件#</th>
                <th>到场时间</th>
                <th>流水号</th>
                <th>原文</th>
                <th>内容哈希</th>
              </tr>
            </thead>
            <tbody>
              {arrivals.map((a) => (
                <tr key={a.id}>
                  <td>#{a.id}</td>
                  <td>
                    <Ts value={a.arrival_time} />
                  </td>
                  <td className="mono">{a.source_ref}</td>
                  <td>{a.raw_text}</td>
                  <td className="hash">{a.content_hash.slice(0, 20)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2 style={{ marginTop: 14 }}>处理场签收证据</h2>
        {signoffs.length === 0 ? (
          <p className="muted">无签收证据</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>事件#</th>
                <th>签收时间</th>
                <th>场地</th>
                <th>重量</th>
                <th>调整单</th>
                <th>内容哈希</th>
              </tr>
            </thead>
            <tbody>
              {signoffs.map((s) => (
                <tr key={s.id}>
                  <td>#{s.id}</td>
                  <td>
                    <Ts value={s.signoff_time} />
                  </td>
                  <td>{s.site_id}</td>
                  <td>{s.weight_tons != null ? `${s.weight_tons} t` : '—'}</td>
                  <td>{s.adjusted ? <Badge value="open" /> : '—'}</td>
                  <td className="hash">{s.content_hash.slice(0, 20)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>④ 关联投诉（{complaints.length}）</h2>
        {complaints.length === 0 ? (
          <p className="muted">无</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>报告人</th>
                <th>事件时间</th>
                <th>状态</th>
                <th>合并组</th>
                <th>核实说明</th>
              </tr>
            </thead>
            <tbody>
              {complaints.map((c) => (
                <tr key={c.id}>
                  <td>#{c.id}</td>
                  <td>{c.reporter}</td>
                  <td>
                    <Ts value={c.event_time} />
                  </td>
                  <td>
                    <Badge value={c.status} />
                  </td>
                  <td>{c.merge_group_id ? `#${c.merge_group_id}` : '—'}</td>
                  <td>{c.verify_note || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h2 style={{ marginTop: 14 }}>⑤ 承运商申辩与主管裁定（并列保存）</h2>
        {appeals.length === 0 ? (
          <p className="muted">无申辩</p>
        ) : (
          appeals.map((a) => (
            <div key={a.id} className="panel" style={{ marginBottom: 10 }}>
              <div className="kv">
                <dt>申辩 #{a.id}</dt>
                <dd>
                  {a.carrier_ref} · 针对 {VIOLATION_LABEL[a.violation_type] || a.violation_type} · <Badge value={a.status} />
                </dd>
                <dt>申辩理由</dt>
                <dd>{a.reason}</dd>
                <dt>主管裁定</dt>
                <dd>
                  {a.ruling ? (
                    <>
                      <Badge value={a.ruling.outcome} /> {a.ruling.ruling_note}
                      <small className="muted">（{a.ruling.ruled_by}）</small>
                    </>
                  ) : (
                    <span className="muted">待主管裁定</span>
                  )}
                </dd>
              </div>
            </div>
          ))
        )}
      </div>

      <Link to="/performance" className="small">
        ← 返回服务绩效
      </Link>
    </div>
  )
}
