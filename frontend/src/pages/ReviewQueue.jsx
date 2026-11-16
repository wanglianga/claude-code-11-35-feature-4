import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { Json } from '../components/Format'

export default function ReviewQueue() {
  const [queue, setQueue] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/api/review-queue').then(setQueue).catch((e) => setError(e.message))
  }, [])

  if (!queue) return <div>{error ? <div className="alert error">{error}</div> : '加载中…'}</div>

  return (
    <div>
      <h1 className="page-title">复核队列</h1>
      <p className="page-desc">财务分析关注：哪些金额仍需复核、为什么 —— 冻结关联、待核冲突、复审中计量事实</p>

      <div className="panel">
        <h2>冻结关联（双批次争用，关系册已冻结分配）</h2>
        {queue.frozen_links.length === 0 ? (
          <p className="muted">无</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>关联ID</th>
                <th>批次</th>
                <th>证据事件</th>
                <th>类型</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              {queue.frozen_links.map((l) => (
                <tr key={l.id}>
                  <td>#{l.id}</td>
                  <td>
                    <Link to={`/batches/${l.batch_id}`}>{l.batch_id}</Link>
                  </td>
                  <td>#{l.event_id}</td>
                  <td>{l.link_type}</td>
                  <td>{l.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="muted">处理方式：进入批次详情「证据链与关联」，对归属明确的关联执行确认，其余拒绝。</p>
      </div>

      <div className="panel">
        <h2>待核冲突（计量事实未签发）</h2>
        {queue.pending_facts.length === 0 ? (
          <p className="muted">无</p>
        ) : (
          queue.pending_facts.map((f) => (
            <div key={f.id} style={{ marginBottom: 12 }}>
              <Link to={`/batches/${f.batch_id}`}>{f.batch_id}</Link> · 计量事实 v{f.version}
              <Json data={f.conflicts} />
            </div>
          ))
        )}
      </div>

      <div className="panel">
        <h2>复审中（校准更正影响，新计费视图暂停引用）</h2>
        {queue.under_review_facts.length === 0 ? (
          <p className="muted">无</p>
        ) : (
          queue.under_review_facts.map((f) => (
            <div key={f.id} style={{ marginBottom: 12 }}>
              <Link to={`/batches/${f.batch_id}`}>{f.batch_id}</Link> · 计量事实 v{f.version}
              <Json data={f.conflicts} />
              <p className="muted">处理方式：进入批次详情重新「执行计量裁定」，系统将适用更正后的校准系数并签发新版本。</p>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
