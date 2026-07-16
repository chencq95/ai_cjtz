import { PlusOutlined, RobotOutlined } from '@ant-design/icons'
import { App, Alert, Button, Card, Form, Input, InputNumber, Modal, Select, Table, Tabs, Tag } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import dayjs from 'dayjs'
import { api, post } from '../api'
import type { User } from '../types'

export default function ReviewsPage({ user }: { user: User }) {
  const [mappingOpen, setMappingOpen] = useState(false)
  const [mappingForm] = Form.useForm()
  const client = useQueryClient()
  const { message } = App.useApp()
  const reviews = useQuery<any[]>({ queryKey: ['reviews', 'accepted'], queryFn: () => api('/v1/reviews?review_status=accepted') })
  const mappings = useQuery<any[]>({ queryKey: ['mappings'], queryFn: () => api('/v1/mappings') })
  const addMapping = useMutation({ mutationFn: (body: any) => post('/v1/mappings', body), onSuccess: () => { message.success('映射已添加'); setMappingOpen(false); mappingForm.resetFields(); client.invalidateQueries({ queryKey: ['mappings'] }) }, onError: (error: Error) => message.error(error.message) })
  const reviewTable = <><Alert showIcon icon={<RobotOutlined />} type="success" message="自动审核已启用" description="低置信分类由来源字段、站点映射和通用字典自动决策并保留审计记录，不再进入人工待办。" style={{ marginBottom: 16 }} /><Table rowKey="id" dataSource={reviews.data || []} loading={reviews.isLoading} columns={[
    { title: '条目 ID', dataIndex: 'item_id', render: (value: string) => <span className="mono">{value.slice(0, 8)}</span> },
    { title: '字段', dataIndex: 'field', render: (value: string) => <Tag>{value}</Tag> },
    { title: '自动决策值', dataIndex: 'proposed_value' },
    { title: '置信度', dataIndex: 'confidence', render: (value: number) => `${Math.round(value * 100)}%` },
    { title: '决策方式', dataIndex: 'reviewer', render: (value: string) => <Tag color="green">{value || 'automatic_classifier'}</Tag> },
    { title: '处理时间', dataIndex: 'created_at', render: (value: string) => dayjs(value).format('MM-DD HH:mm') },
  ]} /></>
  const mappingTable = <><div style={{ textAlign: 'right', marginBottom: 12 }}><Button icon={<PlusOutlined />} disabled={user.role !== 'admin'} onClick={() => { mappingForm.resetFields(); mappingForm.setFieldsValue({ confidence: 1, enabled: true }); setMappingOpen(true) }}>新增映射</Button></div><Table rowKey="id" dataSource={mappings.data || []} loading={mappings.isLoading} columns={[
    { title: '维度', dataIndex: 'dimension_type' }, { title: '来源原值', dataIndex: 'raw_value' }, { title: '规范值', dataIndex: 'normalized_value' }, { title: '置信度', dataIndex: 'confidence', render: (value: number) => `${Math.round(value * 100)}%` }, { title: '启用', dataIndex: 'enabled', render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag> },
  ]} /></>
  return <><div className="page-head"><div><h1>自动分类审核</h1><p>查看自动决策记录并维护可解释的跨站映射规则</p></div><Tag color="green" icon={<RobotOutlined />}>全自动</Tag></div><Card className="panel-card"><Tabs items={[{ key: 'reviews', label: `自动决策记录 (${reviews.data?.length || 0})`, children: reviewTable }, { key: 'mappings', label: '分类映射', children: mappingTable }]} /></Card><Modal title="新增分类映射" open={mappingOpen} onCancel={() => setMappingOpen(false)} onOk={() => mappingForm.submit()} confirmLoading={addMapping.isPending}><Form form={mappingForm} layout="vertical" onFinish={values => addMapping.mutate(values)}><Form.Item name="dimension_type" label="维度" rules={[{ required: true }]}><Select options={['product_type', 'industry', 'region', 'object_kind', 'delivery_method'].map(value => ({ value }))} /></Form.Item><Form.Item name="raw_value" label="来源原值" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="normalized_value" label="规范值" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="confidence" label="置信度"><InputNumber min={0} max={1} step={0.05} /></Form.Item></Form></Modal></>
}
