import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, getUser } from '../api'
import Badge from '../components/Badge'
import { Json, LINK_TYPE_LABEL, Money, SOURCE_TYPE_LABEL, Ts, Weight } from '../components/Format'

const TABS = [
  ['evidence', '证据链与关联'],
  ['facts', '计量裁定与金额'],
  ['publish', '发布与离场核查'],
  ['trace', '审计追溯'],
]

export default function BatchDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('evidence')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const analyst = getUser()?.role === 'analyst'

  const load = useCallback(() => {
    api.get(`/api/batches/${id}`).then(setData).catch((e) => setError(e.message))
  }, [id])
  useEffect(load, [load])

  async function act(path, label) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await api.post(`/api/batches/${id}/${path}`)
      setNotice(`${label}完成`)
      load()
    } catch (e) {
      setError(`${label}失败：${e.message}`)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <div>{error ? <div className="alert error">{error}</div> : '加载中…'}</div>
  const fact = data.measurement_facts[0]
  const amount = data.amount_facts.find((a) => a.status === 'issued')

  return (
    <div>
      <h1 className="page-title">批次 {data.id}</h1>
      <p className="page-desc">
        {data.contract.name} · 车辆 {data.vehicle_id} · 服务时间 <Ts value={data.service_time} />
      </p>
      {error && <div className="alert error">{error}</div>}
      {notice && <div className="alert ok">{notice}</div>}

      <div className="cards">
        <div className="card">
          <div className="k">裁定策略</div>
          <div className="v" style={{ fontSize: 16 }}>
            <Badge value={data.strategy} />
          </div>
        </div>
        <div className="card">
          <div className="k">计量状态</div>
          <div className="v" style={{ fontSize: 16 }}>
            <Badge value={fact?.status || data.status} />
          </div>
        </div>
        <div className="card">
          <div className="k">计费重量 / 可接受区间</div>
          <div className="v" style={{ fontSize: 16 }}>
            {fact?.billing_weight != null ? (
              <Weight value={fact.billing_weight} />
            ) : fact?.acceptable_low != null ? (
              <span>
                [<Weight value={fact.acceptable_low} /> ~ <Weight value={fact.acceptable_high} />]
              </span>
            ) : (
              '—'
            )}
          </div>
        </div>
        <div className="card">
          <div className="k">金额（已确认 / 未决范围）</div>
          <div className="v" style={{ fontSize: 16 }}>
            {amount?.amount != null ? (
              <Money value={amount.amount} />
            ) : amount?.amount_low != null ? (
              <span>
                <Money value={amount.amount_low} /> ~ <Money value={amount.amount_high} />
              </span>
            ) : (
              '未决'
            )}
          </div>
        </div>
      </div>

      {analyst && (
        <div className="panel">
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => act('adjudicate', '计量裁定')}>
              执行计量裁定
            </button>
            <button disabled={busy} onClick={() => act('compile', '费率编译')}>
              费率编译（新解释版本）
            </button>
            <button disabled={busy} onClick={() => act('publish', '差异发布')}>
              发布计费依据
            </button>
            <button disabled={busy} onClick={() => act('departure-check', '离场核查')}>
              签收后离场核查
            </button>
          </div>
        </div>
      )}

      <div className="tabs">
        {TABS.map(([key, label]) => (
          <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === 'evidence' && <EvidenceTab data={data} analyst={analyst} reload={load} />}
      {tab === 'facts' && <FactsTab data={data} />}
      {tab === 'publish' && <PublishTab data={data} />}
      {tab === 'trace' && <TraceTab data={data} />}
    </div>
  )
}

function EvidenceTab({ data, analyst, reload }) {
  const [eventId, setEventId] = useState('')
  const [linkType, setLinkType] = useState('weighing')
  const [msg, setMsg] = useState('')

  async function addLink() {
    setMsg('')
    try {
      const r = await api.post(`/api/batches/${data.id}/links`, {
        event_id: Number(eventId),
        link_type: linkType,
      })
      setMsg(r.status === 'frozen' ? '已加入候选，但因争用被冻结' : '已加入候选关联')
      reload()
    } catch (e) {
      setMsg(`关联失败：${e.message}`)
    }
  }

  async function decide(linkId, decision) {
    const reason = window.prompt('请填写处理原因（审计留痕）') || ''
    try {
      await api.post(`/api/links/${linkId}/${decision}`, { reason })
      reload()
    } catch (e) {
      setMsg(`操作失败：${e.message}`)
    }
  }

  return (
    <div className="panel">
      <h2>
        证据链（批次关系册）
        <span className="tip">候选 → 确认 / 拒绝；同一称重被多批次引用时冻结分配</span>
      </h2>
      {msg && <div className="alert ok">{msg}</div>}
      <table>
        <thead>
          <tr>
            <th>关联</th>
            <th>状态</th>
            <th>证据类型</th>
            <th>原文</th>
            <th>事件时间</th>
            <th>设备/场地</th>
            <th>校准</th>
            <th>重量</th>
            <th>内容哈希</th>
            {analyst && <th>操作</th>}
          </tr>
        </thead>
        <tbody>
          {data.links.map((l) => (
            <tr key={l.id}>
              <td>{LINK_TYPE_LABEL[l.link_type] || l.link_type}</td>
              <td>
                <Badge value={l.status} />
                {l.reason && <div className="muted" style={{ fontSize: 11 }}>{l.reason}</div>}
              </td>
              <td>{SOURCE_TYPE_LABEL[l.event.source_type] || l.event.source_type}</td>
              <td style={{ maxWidth: 220 }}>{l.event.raw_text}</td>
              <td>
                <Ts value={l.event.event_time} />
              </td>
              <td className="mono">
                {l.event.device_id || '—'} / {l.event.site_id || '—'}
              </td>
              <td>{l.event.calibration_version != null ? `v${l.event.calibration_version}` : '—'}</td>
              <td>
                <Weight value={l.event.payload?.weight_tons} />
              </td>
              <td className="hash">{l.event.content_hash.slice(0, 16)}…</td>
              {analyst && (
                <td>
                  {(l.status === 'candidate' || l.status === 'frozen') && (
                    <div className="actions">
                      <button className="small" onClick={() => decide(l.id, 'confirm')}>
                        确认
                      </button>
                      <button className="small" onClick={() => decide(l.id, 'reject')}>
                        拒绝
                      </button>
                    </div>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {analyst && (
        <div className="form-row" style={{ marginTop: 14 }}>
          <label>新增候选关联</label>
          <input
            style={{ width: 110 }}
            placeholder="证据事件ID"
            value={eventId}
            onChange={(e) => setEventId(e.target.value)}
          />
          <select value={linkType} onChange={(e) => setLinkType(e.target.value)}>
            <option value="task">任务</option>
            <option value="weighing">称重</option>
            <option value="receipt">签收</option>
            <option value="gate">门岗</option>
          </select>
          <button className="small primary" onClick={addLink} disabled={!eventId}>
            加入关系册
          </button>
          <span className="muted">事件 ID 可在「来源证据库」页查询</span>
        </div>
      )}
    </div>
  )
}

function FactsTab({ data }) {
  return (
    <div>
      {data.measurement_facts.map((f) => (
        <div className="panel" key={f.id}>
          <h2>
            计量事实 v{f.version} <Badge value={f.status} />
            <span className="tip">{f.note}</span>
          </h2>
          <div className="kv">
            <dt>计费重量</dt>
            <dd>{f.billing_weight != null ? <Weight value={f.billing_weight} /> : '—（区间裁定）'}</dd>
            <dt>可接受区间</dt>
            <dd>
              {f.acceptable_low != null ? (
                <span>
                  [<Weight value={f.acceptable_low} /> ~ <Weight value={f.acceptable_high} />]
                </span>
              ) : (
                '—'
              )}
            </dd>
            <dt>选定证据</dt>
            <dd>{f.selected_event_id != null ? `事件 #${f.selected_event_id}` : '—'}</dd>
            <dt>校准引用</dt>
            <dd>
              {f.calibration_refs.length
                ? f.calibration_refs.map((c, i) => (
                    <div key={i} className="mono">
                      {c.device_id} v{c.version}（{c.status}，×{c.factor}）
                      {c.corrected_to &&
                        ` → 更正适用 v${c.corrected_to.version} ×${c.corrected_to.factor}，调整后 ${c.corrected_to.adjusted_weight}t`}
                    </div>
                  ))
                : '—'}
            </dd>
          </div>
          {f.discrepancy.length > 0 && (
            <>
              <h2 style={{ marginTop: 12 }}>差异佐证（未选定的合格来源）</h2>
              <table>
                <thead>
                  <tr>
                    <th>事件</th>
                    <th>来源</th>
                    <th>重量</th>
                    <th>与计费重量差</th>
                  </tr>
                </thead>
                <tbody>
                  {f.discrepancy.map((d, i) => (
                    <tr key={i}>
                      <td>#{d.event_id}</td>
                      <td>{SOURCE_TYPE_LABEL[d.source_type] || d.source_type}</td>
                      <td>
                        {d.weight != null ? <Weight value={d.adjusted_weight ?? d.weight} /> : `[${d.low} ~ ${d.high}] t`}
                      </td>
                      <td>{d.diff_kg != null ? `${d.diff_kg} kg（${d.diff_pct}%）` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {f.conflicts.length > 0 && (
            <>
              <h2 style={{ marginTop: 12 }}>待核冲突</h2>
              <Json data={f.conflicts} />
            </>
          )}
          {f.exclusions.length > 0 && (
            <>
              <h2 style={{ marginTop: 12 }}>排除原因</h2>
              <Json data={f.exclusions} />
            </>
          )}
        </div>
      ))}
      {data.amount_facts.map((a) => (
        <div className="panel" key={a.id}>
          <h2>
            金额事实·解释版本 v{a.interpretation_version} <Badge value={a.status} />
            <span className="tip">冻结目录 v{a.catalog_version}</span>
          </h2>
          <div className="kv">
            <dt>计费重量</dt>
            <dd>{a.weight_used != null ? <Weight value={a.weight_used} /> : '—（区间）'}</dd>
            <dt>已确认金额</dt>
            <dd>{a.amount != null ? <Money value={a.amount} /> : '未决（不取中点）'}</dd>
            <dt>边界金额</dt>
            <dd>
              {a.amount_low != null ? (
                <span>
                  下界 <Money value={a.amount_low} /> · 上界 <Money value={a.amount_high} />
                  {a.cross_tier && <Badge value="cross_tier" />}
                </span>
              ) : (
                '—'
              )}
            </dd>
            <dt>计量事实</dt>
            <dd>#{a.measurement_fact_id}</dd>
          </div>
        </div>
      ))}
    </div>
  )
}

function PublishTab({ data }) {
  return (
    <div>
      {data.publications.map((p) => (
        <div className="panel" key={p.id}>
          <h2>
            发布 v{p.version} <Badge value={p.state} />
            <span className="tip">下游引用版本 {p.downstream_ref}</span>
          </h2>
          {p.billing_basis ? (
            <>
              <h2>计费依据</h2>
              <Json data={p.billing_basis} />
            </>
          ) : (
            <p className="muted">无计费依据（{p.note || p.state}）</p>
          )}
          {p.pending_ranges && (
            <>
              <h2>未决范围</h2>
              <Json data={p.pending_ranges} />
            </>
          )}
        </div>
      ))}
      {data.departure_checks.map((c) => (
        <div className="panel" key={c.id}>
          <h2>
            签收后离场核查 <Badge value={c.result} />
          </h2>
          <p>{c.detail.message}</p>
          <Json data={c.detail} />
        </div>
      ))}
    </div>
  )
}

function TraceTab({ data }) {
  const fact = data.measurement_facts[0]
  const amount = data.amount_facts.find((a) => a.status === 'issued')
  const pub = data.publications[0]
  return (
    <div className="panel">
      <h2>
        审计追溯链
        <span className="tip">每项费用结论可回查：重量 → 关联 → 合同策略 → 费率 → 校准 → 版本</span>
      </h2>
      <div className="timeline">
        <div className="item">
          <b>① 重量来源</b>
          <div className="muted">
            {fact?.selected_event_id
              ? `选定证据事件 #${fact.selected_event_id}（内容哈希可在证据链页核对）`
              : fact?.acceptable_low != null
                ? `区间裁定：[${fact.acceptable_low} ~ ${fact.acceptable_high}] t，来自 ${fact.discrepancy.length} 份合格重量重叠`
                : '未签发'}
          </div>
        </div>
        <div className="item">
          <b>② 关联</b>
          <div className="muted">
            关系册关联 {data.links.length} 条（确认 {data.links.filter((l) => l.status === 'confirmed').length} ·
            冻结 {data.links.filter((l) => l.status === 'frozen').length} · 拒绝{' '}
            {data.links.filter((l) => l.status === 'rejected').length}）
          </div>
        </div>
        <div className="item">
          <b>③ 合同策略</b>
          <div className="muted">
            {data.contract.id} · {data.contract.contract_type} ·{' '}
            {data.strategy === 'priority' ? '优先级裁定' : '区间裁定'} · 优先级{' '}
            {(data.contract.source_priority || []).join(' > ') || '—'} · 时间容差{' '}
            {data.contract.time_tolerance_minutes} 分钟 · 质量容差 ±{data.contract.quality_tolerance_pct * 100}%
          </div>
        </div>
        <div className="item">
          <b>④ 费率</b>
          <div className="muted">
            {amount
              ? `冻结目录 v${amount.catalog_version}（以服务时间当时有效目录为准），解释版本 v${amount.interpretation_version}`
              : '未编译'}
          </div>
        </div>
        <div className="item">
          <b>⑤ 校准</b>
          <div className="muted">
            {fact?.calibration_refs?.length
              ? fact.calibration_refs
                  .map((c) => `${c.device_id} v${c.version}（${c.status}）`)
                  .join('；')
              : '计量来源未涉及校准版本'}
          </div>
        </div>
        <div className="item">
          <b>⑥ 版本</b>
          <div className="muted">
            计量事实 v{fact?.version ?? '—'} · 金额解释 v{amount?.interpretation_version ?? '—'} · 下游引用{' '}
            {pub?.downstream_ref ?? '未发布'}
          </div>
        </div>
      </div>
    </div>
  )
}
