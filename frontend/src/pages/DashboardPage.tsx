import { Alert, Card, Col, List, Progress, Row, Skeleton, Space, Tag, Typography } from 'antd'
import { AlertOutlined, DatabaseOutlined, PartitionOutlined, RiseOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import { api } from '../api'
import type { Platform } from '../types'

export default function DashboardPage() {
  const summary = useQuery<any>({ queryKey: ['dashboard'], queryFn: () => api('/v1/dashboard'), refetchInterval: 30_000 })
  const platforms = useQuery<Platform[]>({ queryKey: ['platforms'], queryFn: () => api('/v1/platforms') })
  const alerts = useQuery<any[]>({ queryKey: ['alerts'], queryFn: () => api('/v1/alerts') })
  if (summary.isLoading) return <Skeleton active />
  if (summary.isError) return <Alert type="error" message="总览加载失败" description={(summary.error as Error).message} />
  const data = summary.data || {}
  const statusCounts = (platforms.data || []).reduce<Record<string, number>>((acc, item) => { acc[item.onboarding_status] = (acc[item.onboarding_status] || 0) + 1; return acc }, {})
  const chart = {
    tooltip: { trigger: 'item' }, legend: { bottom: 0, icon: 'circle' },
    color: ['#14b8a6','#f2b84b','#e66a6a','#94a3b8'],
    series: [{ type: 'pie', radius: ['52%','75%'], center: ['50%','44%'], label: { show: false }, data: [
      { name: '已激活', value: statusCounts.active || 0 }, { name: '待核验', value: statusCounts.pending_audit || 0 },
      { name: '阻塞/离线', value: (statusCounts.blocked || 0) + (statusCounts.offline || 0) }, { name: '范围外', value: statusCounts.out_of_scope || 0 },
    ] }],
  }
  const metric = (label: string, value: number, foot: string, icon: React.ReactNode, accent: string) => (
    <Card className="metric-card" style={{ '--accent': accent } as React.CSSProperties}><Space align="start" style={{ justifyContent: 'space-between', width: '100%' }}><div><div className="metric-label">{label}</div><div className="metric-value">{value.toLocaleString()}</div><div className="metric-foot">{foot}</div></div><div style={{ color: accent, fontSize: 22 }}>{icon}</div></Space></Card>
  )
  return <>
    <div className="page-head"><div><h1>运行总览</h1><p>全国数据交易平台公开目录的实时健康与变化态势</p></div><Tag color="cyan">Asia/Shanghai · 每日 02:30</Tag></div>
    <Row gutter={[16,16]}>
      <Col span={6}>{metric('来源平台', data.platforms || 0, `${data.active_platforms || 0} 个已启用采集`, <PartitionOutlined />, '#0f766e')}</Col>
      <Col span={6}>{metric('有效目录条目', data.active_items || 0, '产品、组件与场景', <DatabaseOutlined />, '#2563eb')}</Col>
      <Col span={6}>{metric('24h 数据变化', (data.items_added_24h || 0) + (data.items_updated_24h || 0), `新增 ${data.items_added_24h || 0} · 更新 ${data.items_updated_24h || 0}`, <RiseOutlined />, '#ca8a04')}</Col>
      <Col span={6}>{metric('待处理告警', data.open_alerts || 0, '仅站内通知', <AlertOutlined />, '#dc2626')}</Col>
    </Row>
    <Row gutter={[16,16]} style={{ marginTop: 16 }}>
      <Col span={9}><Card title="平台接入状态" className="panel-card"><ReactECharts option={chart} style={{ height: 290 }} /></Card></Col>
      <Col span={15}><Card title="最近一次采集" className="panel-card" extra={data.latest_run ? <span className={`status-pill status-${data.latest_run.status}`}>{data.latest_run.status}</span> : null}>
        {data.latest_run ? <>
          <Row gutter={20}><Col span={8}><Typography.Text type="secondary">运行模式</Typography.Text><Typography.Title level={4}>{data.latest_run.mode === 'full' ? '完整校准' : '增量采集'}</Typography.Title></Col><Col span={8}><Typography.Text type="secondary">开始时间</Typography.Text><Typography.Title level={4}>{dayjs(data.latest_run.started_at).format('MM-DD HH:mm')}</Typography.Title></Col><Col span={8}><Typography.Text type="secondary">完成时间</Typography.Text><Typography.Title level={4}>{data.latest_run.finished_at ? dayjs(data.latest_run.finished_at).format('MM-DD HH:mm') : '进行中'}</Typography.Title></Col></Row>
          <Progress percent={data.latest_run.status === 'running' ? 58 : 100} status={data.latest_run.status === 'failed' ? 'exception' : 'active'} strokeColor="#0f766e" />
          <Row gutter={12} style={{ marginTop: 18 }}>{Object.entries(data.latest_run.stats || {}).slice(0,6).map(([key,value]) => <Col span={8} key={key}><div className="metric-label">{key}</div><b>{String(value)}</b></Col>)}</Row>
        </> : <Alert type="info" message="尚未执行采集任务" showIcon />}
      </Card></Col>
      <Col span={24}><Card title="站内告警" className="panel-card"><List dataSource={(alerts.data || []).slice(0,8)} locale={{ emptyText: '当前没有未处理告警' }} renderItem={(item:any) => <List.Item><List.Item.Meta avatar={<AlertOutlined style={{ color: item.severity === 'error' ? '#dc2626' : '#ca8a04' }} />} title={<Space><span>{item.title}</span><Tag>{item.type}</Tag></Space>} description={`${item.message || '—'} · ${dayjs(item.created_at).format('MM-DD HH:mm')}`} /></List.Item>} /></Card></Col>
    </Row>
  </>
}
