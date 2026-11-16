import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, saveAuth } from '../api'

export default function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await api.login(username, password)
      saveAuth(data.token, data.user)
      navigate('/')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-box" onSubmit={submit}>
        <h1>收运证据审计与承运商 SLA 绩效服务</h1>
        <div className="sub">来源证据 · 批次关联 · 计量裁定 · 费率编译 · 差异发布 · SLA 评分</div>
        {error && <div className="alert error">{error}</div>}
        <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button className="primary" disabled={loading}>
          {loading ? '登录中…' : '登录'}
        </button>
        <div className="hint">
          analyst / Analyst@2026（数据团队，证据录入与评分草稿，可写）
          <br />
          auditor / Auditor@2026（财务复核，全局只读）
          <br />
          supervisor / Supervisor@2026（物业主管，合同确认、投诉核实、裁定、月结锁定）
        </div>
      </form>
    </div>
  )
}
