import { App, Button, Descriptions, Drawer, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, Tooltip } from 'antd'
import { CheckCircleOutlined, EditOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, patch, post } from '../api'
import type { Platform, User } from '../types'

const status = (value?: string) => <span className={`status-pill status-${value || 'unknown'}`}>{value || 'unknown'}</span>

export default function PlatformsPage({ user }: { user: User }) {
  const [editing, setEditing] = useState<Platform | null>(null)
  const [detail, setDetail] = useState<Platform | null>(null)
  const [form] = Form.useForm()
  const client = useQueryClient()
  const { message } = App.useApp()
  const platforms = useQuery<Platform[]>({ queryKey: ['platforms'], queryFn: () => api('/v1/platforms') })
  const collections = useQuery<any[]>({ queryKey: ['collections', detail?.id], queryFn: () => api(`/v1/collections?platform_id=${detail!.id}`), enabled: !!detail })
  const update = useMutation({ mutationFn: ({ id, values }: { id:number; values:any }) => patch(`/v1/platforms/${id}`, values), onSuccess: () => { message.success('平台配置已保存'); setEditing(null); client.invalidateQueries({ queryKey: ['platforms'] }) }, onError: (e:Error) => message.error(e.message) })
  const check = useMutation({ mutationFn: (id:number) => post<any>(`/v1/platforms/${id}/check`), onSuccess: data => message[data.ok ? 'success' : 'warning'](data.ok ? `连通成功：HTTP ${data.status_code}` : data.error), onError: (e:Error) => message.error(e.message) })
  const columns = [
    { title: '#', dataIndex: 'id', width: 55 },
    { title: '地区', width: 100, render: (_:unknown,row:Platform) => row.city || row.province },
    { title: '平台名称', dataIndex: 'name', width: 230, render: (value:string,row:Platform) => <span className="table-link" onClick={() => setDetail(row)}>{value}</span> },
    { title: '接入状态', dataIndex: 'onboarding_status', width: 120, render: status },
    { title: '站点状态', dataIndex: 'url_status', width: 145, render: (value:string) => <Tag>{value}</Tag> },
    { title: '目录条目', dataIndex: 'active_items', width: 100, align: 'right' as const },
    { title: '最近采集', width: 120, render: (_:unknown,row:Platform) => row.last_run ? status(row.last_run.status) : <span className="muted">尚未运行</span> },
    { title: '覆盖', width: 110, render: (_:unknown,row:Platform) => row.last_run ? status(row.last_run.coverage) : status('unknown') },
    { title: '启用', dataIndex: 'enabled', width: 70, render: (value:boolean) => <Switch size="small" checked={value} disabled /> },
    { title: '操作', fixed: 'right' as const, width: 125, render: (_:unknown,row:Platform) => <Space><Tooltip title="连通检查"><Button type="text" icon={<ReloadOutlined />} loading={check.isPending} disabled={user.role !== 'admin' || !row.canonical_url} onClick={() => check.mutate(row.id)} /></Tooltip><Tooltip title="编辑配置"><Button type="text" icon={<EditOutlined />} disabled={user.role !== 'admin'} onClick={() => { setEditing(row); form.setFieldsValue(row) }} /></Tooltip><Tooltip title="打开官网"><Button type="text" icon={<LinkOutlined />} disabled={!row.canonical_url} onClick={() => window.open(row.canonical_url,'_blank','noopener')} /></Tooltip></Space> },
  ]
  return <>
    <div className="page-head"><div><h1>平台管理</h1><p>38 个来源的官方入口、栏目、适配器与采集安全策略</p></div><Tag color="blue">{platforms.data?.filter(item => item.enabled).length || 0} 个已启用</Tag></div>
    <Table rowKey="id" loading={platforms.isLoading} dataSource={platforms.data || []} columns={columns} scroll={{ x: 1320 }} pagination={{ pageSize: 20 }} className="panel-card" />
    <Modal title="编辑平台配置" open={!!editing} onCancel={() => setEditing(null)} onOk={() => form.validateFields().then(values => update.mutate({ id: editing!.id, values }))} confirmLoading={update.isPending} width={680}>
      <Form form={form} layout="vertical"><Form.Item label="平台名称" name="name"><Input /></Form.Item><Form.Item label="规范官网" name="canonical_url"><Input /></Form.Item><Space size="large"><Form.Item label="接入状态" name="onboarding_status"><Select style={{ width:180 }} options={['pending_audit','active','blocked','offline','out_of_scope'].map(value => ({ value }))} /></Form.Item><Form.Item label="渲染模式" name="render_mode"><Select style={{ width:160 }} options={['auto','http','browser'].map(value => ({ value }))} /></Form.Item><Form.Item label="启用" name="enabled" valuePropName="checked"><Switch /></Form.Item></Space><Space size="large"><Form.Item label="每秒请求" name="rate_limit"><InputNumber min={0.1} max={100} step={0.1} /></Form.Item><Form.Item label="最大并发" name="max_concurrency"><InputNumber min={1} max={32} /></Form.Item><Form.Item label="适配器" name="adapter"><Input /></Form.Item></Space><Form.Item label="备注" name="notes"><Input.TextArea rows={3} /></Form.Item></Form>
    </Modal>
    <Drawer title={detail?.name} open={!!detail} onClose={() => setDetail(null)} width={820}>
      {detail && <><Descriptions column={2} bordered size="small" items={[{key:'region',label:'地区',children:`${detail.province} ${detail.city}`},{key:'status',label:'接入状态',children:status(detail.onboarding_status)},{key:'url',label:'规范官网',span:2,children:<a href={detail.canonical_url} target="_blank">{detail.canonical_url || '未提供'}</a>},{key:'adapter',label:'适配器',children:detail.adapter},{key:'limit',label:'速率/并发',children:`${detail.rate_limit} req/s · ${detail.max_concurrency}`},{key:'notes',label:'核验备注',span:2,children:detail.notes || '—'}]} /><h3 style={{marginTop:28}}>采集栏目</h3><Table size="small" rowKey="id" loading={collections.isLoading} dataSource={collections.data || []} pagination={false} columns={[{title:'栏目',dataIndex:'name'},{title:'类型',dataIndex:'object_kind'},{title:'启用',dataIndex:'enabled',render:(v:boolean)=><Switch size="small" checked={v} disabled />},{title:'覆盖状态',dataIndex:'coverage_status',render:status},{title:'预期数量',dataIndex:'expected_count',render:(v:number)=>v ?? '待核验'},{title:'适配器',render:(_:unknown,r:any)=>`${r.adapter}@${r.adapter_version}`}]} /></>}
    </Drawer>
  </>
}
