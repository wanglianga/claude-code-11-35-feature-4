import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getUser } from '../api'
import Badge from '../components/Badge'
import { ADJUSTMENT_TYPE_LABEL, Ts, VIOLATION_LABEL } from '../components/Format'

const MONTH = '2026-11'

export default function Performance() {
  const user = getUser()
  const analyst = user?.role === 'analyst'
  const supervisor = user?.role === 'supervisor'
  const [contracts, setContracts] = useState([])
  const [contractId, setContractId] = useState('')
  const [mode, setMode] = useState('incremental')
  const [score, setScore] = useState(null)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .get('/api/contracts')
      .then((rows) => {
        const confirmed = rows.filter((c) => c.confirmed)
        setContracts(confirmed)
        if (confirmed.length && !contractId) setContractId(confirmed[0].id)
      })
      .catch((e) => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const load = useCallback(() => {
    if (!contractId) return
    api
      .get(`/api/sla/scoring?contract_id=${contractId}&month=${MONTH}&mode=${mode}`)
      .then(setScore)
      .catch(() => setScore(null))
    if (mode === 'month_end') {
      api.get(`/api/sla/progress?contract_id=${contractId}&month=${MONTH}`).then(setProgress).catch(() => {})
    } else {
      setProgress(null)
    }
  }, [contractId, mode])

  useEffect(load, [load])

  async function act(path, body, label) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const r = await api.post(`/api/sla/${path}`, body)
      setNotice(`${label}完成`)
      if (r && r.id) setScore(r)
      else load()
    } catch (e) {
      setError(`${label}失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  async function applyAdj(adjId) {
    setBusy(true)
    setError('')
    try {
      await api.post(`/api/sla/adjustments/${adjId}/apply`)
      setNotice('调整单已形成调整版本，原凭证未改写')
      load()
    } catch (e) {
      setError(`调整失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <h1 className="page-title">承运商服务水平（SLA）月度评分</h1>
      <p className="page-desc">
        仅已确认合同与证据支持的任务进入评分；未核投诉只列风险。同一合同窗口与任务证据支持两种核算模式
      </p>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert ok">{notice}</div>}

      <div className="form-row">
        <label>已确认合同</label>
        <select value={contractId} onChange={(e) => setContractId(e.target.value)}>
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id} · {c.name}
            </option>
          ))}
        </select>
        <label>核算模式</label>
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="incremental">增量更新（证据到达即算，记录受影响车次）</option>
          <option value="month_end">月结统一（锁定后计算，稳定可复核）</option>
        </select>
      </div>

      {!score ? (
        <div className="panel">
          <p className="muted">该合同/月份/模式尚无评分。</p>
          {analyst && contractId && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => act('scoring/run', { contract_id: contractId, month: MONTH, mode }, '生成评分草稿')}
            >
              生成评分草稿
            </button>
          )}
          {mode === 'month_end' && progress && <ProgressCard progress={progress} />}
        </div>
      ) : (
        <>
          {score.review_required && (
            <div className="alert error">
              ⚠ {score.review_reason}。请主管重新查看受影响车次后重算评分，再行发布。
            </div>
          )}

          <div className="cards">
            <div className="card">
              <div className="k">当前分数（v{score.version}）</div>
              <div className="v">{score.score}</div>
            </div>
            <div className="card">
              <div className="k">累计扣罚</div>
              <div className="v">{score.total_deduction} 分</div>
            </div>
            <div className="card">
              <div className="k">评分状态</div>
              <div className="v" style={{ fontSize: 16 }}>
                <Badge value={score.status} /> <Badge value={score.payment_status} />
              </div>
            </div>
            <div className="card">
              <div className="k">调整后分数</div>
              <div className="v">
                {score.adjusted_score}
                <small>（{score.adjustments.length} 笔调整）</small>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="actions">
              {analyst && score.status !== 'locked' && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => act('scoring/run', { contract_id: contractId, month: MONTH, mode }, '评分重算')}
                >
                  {mode === 'incremental' ? '按最新证据增量重算' : '重新计算月结草稿'}
                </button>
              )}
              {analyst && score.status === 'draft' && (
                <button disabled={busy} onClick={() => act('scoring/publish', { contract_id: contractId, month: MONTH, mode }, '发布评分')}>
                  发布评分
                </button>
              )}
              {supervisor && score.status !== 'locked' && (
                <button
                  disabled={busy}
                  onClick={() => {
                    const ref = window.prompt('月结证据批次号', `BATCH-${MONTH}-${contractId}`)
                    if (ref !== null) act('scoring/lock', { contract_id: contractId, month: MONTH, mode, evidence_batch_ref: ref }, '月结锁定')
                  }}
                >
                  月结锁定
                </button>
              )}
              {supervisor && score.status === 'locked' && score.payment_status === 'unpaid' && (
                <button disabled={busy} onClick={() => act('scoring/mark-paid', { contract_id: contractId, month: MONTH, mode }, '付款登记')}>
                  登记罚款已支付
                </button>
              )}
            </div>
            {score.evidence_batch_ref && (
              <p className="muted" style={{ marginTop: 8 }}>
                月结证据批次：<span className="mono">{score.evidence_batch_ref}</span>
                {score.locked_at && (
                  <>
                    {' '}· 锁定人 {score.locked_by} · <Ts value={score.locked_at} />
                  </>
                )}
              </p>
            )}
          </div>

          {mode === 'month_end' && progress && <ProgressCard progress={progress} />}

          <div className="panel">
            <h2>
              评分版本流水
              <span className="tip">同一月份分数会多次变化，每版记录受影响车次；发布/锁定记录不可变</span>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>版本</th>
                  <th>分数</th>
                  <th>扣罚</th>
                  <th>受影响车次</th>
                  <th>变更说明</th>
                  <th>时间</th>
                </tr>
              </thead>
              <tbody>
                {[...score.versions].reverse().map((v) => (
                  <tr key={v.id}>
                    <td>v{v.version}</td>
                    <td>{v.score}</td>
                    <td>{v.total_deduction}</td>
                    <td>
                      {v.affected_task_ids.length === 0 ? (
                        <span className="muted">无变化</span>
                      ) : (
                        v.affected_task_ids.map((tid) => (
                          <Link key={tid} to={`/sla/tasks/${tid}`} className="mono" style={{ marginRight: 6 }}>
                            {tid}
                          </Link>
                        ))
                      )}
                    </td>
                    <td>{v.change_note}</td>
                    <td>
                      <Ts value={v.created_at} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="panel">
            <h2>
              任务级评分明细（{score.details.length} 个合格车次）
              <span className="tip">点击车次下钻合同条款、任务证据与裁决记录</span>
            </h2>
            <table>
              <thead>
                <tr>
                  <th>车次</th>
                  <th>车辆/地址</th>
                  <th>承诺时间</th>
                  <th>违约</th>
                  <th>扣罚</th>
                  <th>证据完整</th>
                  <th>下钻</th>
                </tr>
              </thead>
              <tbody>
                {score.details.map((d) => (
                  <tr key={d.task_id}>
                    <td className="mono">{d.task_id}</td>
                    <td>
                      {d.vehicle_id}
                      <small className="muted"> {d.address}</small>
                    </td>
                    <td>
                      <Ts value={d.scheduled_time} />
                    </td>
                    <td>
                      {d.violations.length === 0 ? (
                        <span className="muted">合规</span>
                      ) : (
                        d.violations.map((v) => (
                          <span key={v.type} style={{ marginRight: 6 }}>
                            {VIOLATION_LABEL[v.type] || v.type}（{v.deduction}）
                          </span>
                        ))
                      )}
                    </td>
                    <td>{d.deduction}</td>
                    <td>{d.evidence_complete ? '是' : '否'}</td>
                    <td>
                      <Link to={`/sla/tasks/${d.task_id}`}>查看</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {score.risks.length > 0 && (
            <div className="panel">
              <h2>
                风险与缺口（不直接扣分）
                <span className="tip">未核投诉、证据缺口、被排除任务在此列示</span>
              </h2>
              <table>
                <thead>
                  <tr>
                    <th>类型</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {score.risks.map((r, i) => (
                    <tr key={i}>
                      <td className="mono">{r.type}</td>
                      <td>{r.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="panel">
            <h2>
              月结调整单
              <span className="tip">锁定/付款后的迟到签收、追溯裁定只追加调整，旧付款凭证不改写</span>
            </h2>
            {score.adjustments.length === 0 ? (
              <p className="muted">无调整</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>车次</th>
                    <th>类型</th>
                    <th>扣罚差额</th>
                    <th>原因</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {score.adjustments.map((a) => (
                    <tr key={a.id}>
                      <td>{a.id}</td>
                      <td>
                        <Link to={`/sla/tasks/${a.task_id}`}>{a.task_id}</Link>
                      </td>
                      <td>{ADJUSTMENT_TYPE_LABEL[a.adjustment_type] || a.adjustment_type}</td>
                      <td>{a.delta_deduction > 0 ? `+${a.delta_deduction}` : a.delta_deduction} 分</td>
                      <td style={{ maxWidth: 320 }}>{a.reason}</td>
                      <td>
                        <Badge value={a.status} />
                      </td>
                      <td>
                        {a.status === 'open' && supervisor && (
                          <button className="small" disabled={busy} onClick={() => applyAdj(a.id)}>
                            形成调整版本
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function ProgressCard({ progress }) {
  return (
    <div className="panel">
      <h2>
        月内进度与证据缺口（月结模式）
        <span className="tip">月内只展示进度和缺口，不显示稳定分数；证据完整度 {progress.evidence_complete_pct}%</span>
      </h2>
      <div className="cards">
        <div className="card">
          <div className="k">排班车次</div>
          <div className="v">{progress.total_tasks}</div>
        </div>
        <div className="card">
          <div className="k">到场覆盖</div>
          <div className="v">{progress.arrival_covered}</div>
        </div>
        <div className="card">
          <div className="k">签收覆盖</div>
          <div className="v">{progress.signoff_covered}</div>
        </div>
        <div className="card">
          <div className="k">未核投诉</div>
          <div className="v">{progress.unverified_complaints}</div>
        </div>
      </div>
      {progress.gaps.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>车次</th>
              <th>缺口</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {progress.gaps.map((g, i) => (
              <tr key={i}>
                <td>
                  <Link to={`/sla/tasks/${g.task_id}`}>{g.task_id}</Link>
                </td>
                <td className="mono">{g.gap}</td>
                <td className="muted">{g.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
