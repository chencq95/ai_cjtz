import { Alert, Card, Progress, Select, Space, Tag, Typography } from 'antd'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { api } from '../api'

const cellStatus = (collection:any) => {
  const colors:Record<string,string>={complete:'green',partial:'gold',blocked:'red',out_of_scope:'default',unknown:'default'}
  const rate=collection?.reconciliation_rate==null?'—':`${(collection.reconciliation_rate*100).toFixed(1)}%`
  return <div><Space><Tag color={colors[collection?.status]||'default'}>{collection?.status||'未建档'}</Tag>{collection?.enabled===false&&<Tag>未启用</Tag>}</Space><div style={{fontSize:12,color:'#64748b',marginTop:7}}>{collection?.expected!=null?`预期 ${collection.expected} · 有效 ${collection.active||0}`:'预期数量待核验'} · 对账 {rate}</div><div style={{fontSize:12,color:'#94a3b8'}}>详情 {collection?.detail_success||0} · 版本 {collection?.version_count||0} · 错误 {collection?.error_count||0}</div></div>
}

export default function CoveragePage() {
  const [filter,setFilter]=useState<string>('all')
  const coverage=useQuery<any[]>({queryKey:['coverage'],queryFn:()=>api('/v1/coverage')})
  const data=useMemo(()=>filter==='all'?(coverage.data||[]):(coverage.data||[]).filter(row=>row.onboarding_status===filter),[coverage.data,filter])
  const complete=(coverage.data||[]).filter(row=>row.conclusion==='COMPLETE').length
  return <>
    <div className="page-head"><div><h1>覆盖率矩阵</h1><p>只有分页闭合、对账通过且无未处理错误的栏目才能标记 COMPLETE</p></div><Select value={filter} onChange={setFilter} style={{width:160}} options={['all','active','pending_audit','blocked','offline','out_of_scope'].map(value=>({value,label:value==='all'?'全部状态':value}))}/></div>
    <Card className="panel-card" style={{marginBottom:16}}><Space size={35}><div><Typography.Text type="secondary">已建档来源</Typography.Text><Typography.Title level={3} style={{margin:0}}>{coverage.data?.length||0}/38</Typography.Title></div><div><Typography.Text type="secondary">最终结论 COMPLETE</Typography.Text><Typography.Title level={3} style={{margin:0}}>{complete}</Typography.Title></div><Progress type="circle" size={72} percent={Math.round(complete/38*100)} strokeColor="#0f766e" /></Space></Card>
    <Alert type="info" showIcon message="受限、失效或缺少官网的来源以明确状态验收，不会被统计为零条数据的成功采集。" style={{marginBottom:16}}/>
    <div className="coverage-grid"><div className="coverage-cell coverage-head">来源平台</div><div className="coverage-cell coverage-head">数据产品</div><div className="coverage-cell coverage-head">数据组件</div><div className="coverage-cell coverage-head">数据场景</div>{data.flatMap(row=>{const find=(kind:string)=>row.collections.find((c:any)=>c.kind===kind);return [<div className="coverage-cell" key={`${row.platform_id}-p`}><b>{row.platform_id}. {row.platform_name}</b><div style={{marginTop:6}}><span className={`status-pill status-${row.onboarding_status}`}>{row.onboarding_status}</span></div></div>,<div className="coverage-cell" key={`${row.platform_id}-product`}>{cellStatus(find('product'))}</div>,<div className="coverage-cell" key={`${row.platform_id}-component`}>{cellStatus(find('component'))}</div>,<div className="coverage-cell" key={`${row.platform_id}-scenario`}>{cellStatus(find('scenario'))}</div>]})}</div>
  </>
}
