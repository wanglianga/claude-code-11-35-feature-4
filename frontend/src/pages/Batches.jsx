import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import Badge from '../components/Badge'
import { Money, Ts, Weight } from '../components/Format'

export default function Batches() {
  const [batches, setBatches] = useState([])
  const [strategy, setStrategy] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    api
      .get(`/api/batches${strategy ? `?strategy=${strategy}` : ''}`)
      .then(setBatches)
      .catch((e) => setError(e.message))
  }, [strategy])

  return (
    <div>
      <h1 className="page-title">收运批次</h1>
      <p className="page-desc">按合同类型选择优先级裁定或区间裁定；点击批次进入完整审计追溯</p>
      {error && <div className="alert error">{error}</div>}
      <div className="form-row">
        <label>裁定策略</label>
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          <option value="">全部</option>
          <option value="priority">优先级裁定</option>
          <option value="interval">区间裁定</option>
        </select>
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr>
              <th>批次号</th>
              <th>合同</th>
              <th>车辆</th>
              <th>服务时间</th>
              <th>策略</th>
              <th>计量状态</th>
              <th>计费重量 / 可接受区间</th>
              <th>金额</th>
              <th>发布</th>
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td>
                  <Link to={`/batches/${b.id}`}>{b.id}</Link>
                </td>
                <td>{b.contract_id}</td>
                <td>{b.vehicle_id}</td>
                <td>
                  <Ts value={b.service_time} />
                </td>
                <td>
                  <Badge value={b.strategy} />
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
                    <span>
                      <Money value={b.amount_low} /> ~ <Money value={b.amount_high} />
                      {b.cross_tier && <Badge value="cross_tier" />}
                    </span>
                  ) : (
                    <span className="muted">未决</span>
                  )}
                </td>
                <td>
                  {b.publication ? (
                    <>
                      <span className="mono">{b.publication}</span> <Badge value={b.publication_state} />
                    </>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
