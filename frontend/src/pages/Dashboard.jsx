import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Badge from '../components/Badge'
import { Money, Weight } from '../components/Format'

export default function Dashboard() {
  const [batches, setBatches] = useState([])
  const [queue, setQueue] = useState(null)
  const [report, setReport] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([
      api.get('/api/batches'),
      api.get('/api/review-queue'),
      api.get('/api/reports/monthly?month=2026-10'),
    ])
      .then(([b, q, r]) => {
        setBatches(b)
        setQueue(q)
        setReport(r)
      })
      .catch((e) => setError(e.message))
  }, [])

  const confirmed = report?.totals?.confirmed_amount ?? 0
  const pendingCount = report?.totals?.pending_count ?? 0

  return (
    <div>
      <h1 className="page-title">总览</h1>
      <p className="page-desc">2026-10 服务月：哪些金额可确认、哪些仍需复核，一屏看清</p>
      {error && <div className="alert error">{error}</div>}

      <div className="cards">
        <div className="card">
          <div className="k">本月批次</div>
          <div className="v">{report?.batches?.length ?? '—'}</div>
        </div>
        <div className="card">
          <div className="k">已确认金额（可入账）</div>
          <div className="v">
            <Money value={confirmed} />
          </div>
        </div>
        <div className="card">
          <div className="k">未决批次（区间/冲突/复审）</div>
          <div className="v">{pendingCount}</div>
        </div>
        <div className="card">
          <div className="k">复核队列</div>
          <div className="v">
            {(queue?.frozen_links?.length ?? 0) +
              (queue?.pending_facts?.length ?? 0) +
              (queue?.under_review_facts?.length ?? 0)}
            <small> 项待处理</small>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>
          最新批次
          <span className="tip">策略已在每行标明：优先级裁定 / 区间裁定</span>
        </h2>
        <table>
          <thead>
            <tr>
              <th>批次号</th>
              <th>合同</th>
              <th>策略</th>
              <th>状态</th>
              <th>计费重量 / 区间</th>
              <th>金额</th>
              <th>发布版本</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td>
                  <Link to={`/batches/${b.id}`}>{b.id}</Link>
                </td>
                <td>{b.contract_id}</td>
                <td>
                  <Badge value={b.strategy === 'priority' ? 'priority' : 'interval'} />
                </td>
                <td>
                  <Badge value={b.fact_status || b.status} />
                </td>
                <td>
                  {b.billing_weight != null ? (
                    <Weight value={b.billing_weight} />
                  ) : b.acceptable_low != null ? (
                    <span>
                      [<Weight value={b.acceptable_low} /> ~ <Weight value={b.acceptable_high} />]
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  {b.amount != null ? (
                    <Money value={b.amount} />
                  ) : b.amount_low != null ? (
                    <span className="muted">
                      <Money value={b.amount_low} /> ~ <Money value={b.amount_high} />
                      {b.cross_tier && ' （跨阶梯）'}
                    </span>
                  ) : (
                    <span className="muted">未决</span>
                  )}
                </td>
                <td className="mono">{b.publication || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {queue && (
        <div className="panel">
          <h2>复核队列摘要</h2>
          <p className="muted">
            冻结关联 {queue.frozen_links.length} 项 · 待核冲突 {queue.pending_facts.length} 项 · 复审中{' '}
            {queue.under_review_facts.length} 项 — <Link to="/review">进入复核队列</Link>
          </p>
        </div>
      )}
    </div>
  )
}
