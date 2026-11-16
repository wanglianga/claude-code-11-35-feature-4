export function Json({ data }) {
  return <pre className="json">{JSON.stringify(data, null, 2)}</pre>
}

export function Money({ value }) {
  if (value === null || value === undefined) return <span className="muted">未决</span>
  return <span>¥ {Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
}

export function Weight({ value }) {
  if (value === null || value === undefined) return <span className="muted">—</span>
  return <span>{Number(value).toFixed(4).replace(/0+$/, '').replace(/\.$/, '')} t</span>
}

export function Ts({ value }) {
  if (!value) return <span className="muted">—</span>
  return <span className="mono">{new Date(value).toLocaleString('zh-CN', { hour12: false })}</span>
}

export const SOURCE_TYPE_LABEL = {
  collection_task: '收运任务',
  gate_record: '门岗记录',
  weighbridge_reading: '地磅读数',
  facility_receipt: '处理场签收',
  rate_catalog: '费率目录',
  accounting_receipt: '账务回执',
}

export const LINK_TYPE_LABEL = {
  task: '任务',
  weighing: '称重',
  receipt: '签收',
  gate: '门岗',
}

export const VIOLATION_LABEL = {
  missed_collection: '漏收',
  late_arrival: '到场超时',
  late_receipt: '迟到签收',
  valid_complaint: '核实投诉',
}

export const COMPLAINT_CATEGORY_LABEL = {
  missed_collection: '漏收',
  spillage: '污水/散落',
  odor: '异味',
  other: '其他',
}

export const ADJUSTMENT_TYPE_LABEL = {
  late_signoff_after_lock: '锁定后迟到签收',
  ruling_after_lock: '锁定后追溯裁定',
  complaint_verified_after_lock: '锁定后投诉核实',
}
