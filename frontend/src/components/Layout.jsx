import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { clearAuth, getUser } from '../api'

const NAV = [
  { to: '/', label: '总览', end: true },
  { to: '/batches', label: '收运批次' },
  { to: '/evidence', label: '来源证据库' },
  { to: '/review', label: '复核队列' },
  { to: '/reports', label: '月结报表' },
  { to: '/performance', label: '服务绩效评分' },
  { to: '/sla', label: '投诉与裁定' },
]

export default function Layout() {
  const user = getUser()
  const navigate = useNavigate()
  const roleLabel =
    user?.role === 'analyst' ? '数据团队' : user?.role === 'supervisor' ? '物业主管' : '财务只读'
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>收运证据审计与<br />承运商 SLA 绩效服务</h1>
        <div className="sub">证据 · 关联 · 裁定 · 费率 · 发布 · SLA 评分</div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'active' : '')}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="user">
          <div>{user?.display_name}</div>
          <div className="muted">{user?.username} · {roleLabel}</div>
          <button
            className="small"
            onClick={() => {
              clearAuth()
              navigate('/login')
            }}
          >
            退出登录
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  )
}
