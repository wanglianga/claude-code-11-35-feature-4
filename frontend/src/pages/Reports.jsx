import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Badge from '../components/Badge'
import { Json, Money, Ts } from '../components/Format'

export default function Reports() {
  const [month, setMonth] = useState('2026-10')
  const [report, setReport] = useState(null)
  const [pubs, setPubs] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .get(`/api/reports/monthly?month=${month}`)
      .then(setReport)
      .catch((e) => setError(e.message))
    api.get('/api/publications').then(setPubs).catch(() => {})
  }, [month])

  return (
    <div>
      <h1 className="page-title">月结报表与发布记录</h1>
      <p className="page-desc">
        同一报表中标明裁定策略；区间裁定的批次以未决范围列示，不伪造已确认金额
      </p>
      {error && <div className="alert error">{error}</div>}

      <div className="form-row">
        <label>服务月份</label>
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
      </div>

      {report && (
        <>
          <div className="cards">
            <div className="card">
              <div className="k">已确认金额合计</div>
              <div className="v">
                <Money value={report.totals.confirmed_amount} />
              </div>
            </div>
            <div className="card">
              <div className="k">已确认批次</div>
              <div className="v">{report.totals.confirmed_count}</div>
            </div>
            <div className="card">
              <div className="k">未决批次</div>
              <div className="v">{report.totals.pending_count}</div>
            </div>
          </div>
          <div className="panel">
            <h2>{report.month} 批次结算视图</h2>
            <table>
              <thead>
                <tr>
                  <th>批次</th>
                  <th>合同</th>
                  <th>策略</th>
                  <th>服务时间</th>
                  <th>计量状态</th>
                  <th>已确认金额</th>
                  <th>未决范围</th>
                  <th>发布版本</th>
                </tr>
              </thead>
              <tbody>
                {report.batches.map((r) => (
                  <tr key={r.batch_id}>
                    <td>
                      <Link to={`/batches/${r.batch_id}`}>{r.batch_id}</Link>
                    </td>
                    <td>{r.contract_id}</td>
                    <td>
                      <Badge value={r.strategy} />
                    </td>
                    <td>
                      <Ts value={r.service_time} />
                    </td>
                    <td>
                      <Badge value={r.fact_status || r.batch_status} />
                    </td>
                    <td>{r.confirmed_amount != null ? <Money value={r.confirmed_amount} /> : <span className="muted">—</span>}</td>
                    <td>
                      {r.pending_range ? (
                        r.pending_range.amount_low != null ? (
                          <span>
                            <Money value={r.pending_range.amount_low} /> ~{' '}
                            <Money value={r.pending_range.amount_high} />
                            {r.pending_range.cross_tier && ' （跨阶梯）'}
                          </span>
                        ) : (
                          <span className="muted">{r.pending_range.reason || '存在待核冲突'}</span>
                        )
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      {r.publication ? (
                        <>
                          <span className="mono">{r.publication}</span> <Badge value={r.publication_state} />
                        </>
                      ) : (
                        <span className="muted">未发布</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <div className="panel">
        <h2>
          发布记录（差异发布器输出）
          <span className="tip">计费依据 + 未决范围 + 下游引用版本；发布记录不可变</span>
        </h2>
        {pubs.map((p) => (
          <details key={p.id} style={{ marginBottom: 10 }}>
            <summary>
              <span className="mono">{p.downstream_ref}</span> · <Link to={`/batches/${p.batch_id}`}>{p.batch_id}</Link> ·{' '}
              <Badge value={p.strategy} /> <Badge value={p.state} /> <Ts value={p.created_at} />
            </summary>
            {p.billing_basis && (
              <>
                <h2 style={{ marginTop: 10 }}>计费依据</h2>
                <Json data={p.billing_basis} />
              </>
            )}
            {p.pending_ranges && (
              <>
                <h2>未决范围</h2>
                <Json data={p.pending_ranges} />
              </>
            )}
          </details>
        ))}
      </div>
    </div>
  )
}
