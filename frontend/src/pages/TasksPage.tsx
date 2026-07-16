import { DeleteOutlined, PauseCircleOutlined, PlayCircleOutlined, RedoOutlined, ScheduleOutlined } from '@ant-design/icons'
import { App, Button, Card, Drawer, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, Timeline, Typography } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import dayjs from 'dayjs'
import { api, post, remove } from '../api'
import type { Platform, Task, User } from '../types'

const state = (value: string) => <span className={`status-pill status-${value}`}>{value}</span>

function LogDrawer({ task, onClose }: { task: Task | null; onClose: () => void }) {
  const [logs, setLogs] = useState<Array<{ id: string; level: string; message: string; created_at: string }>>([])
  useEffect(() => {
    if (!task) return
    setLogs([])
    const stream = new EventSource(`/api/v1/tasks/${task.id}/logs`, { withCredentials: true })
    stream.addEventListener('log', event => {
      const data = JSON.parse((event as MessageEvent).data)
      setLogs(old => [...old, { id: (event as MessageEvent).lastEventId, ...data }])
    })
    stream.addEventListener('end', () => stream.close())
    return () => stream.close()
  }, [task])
  return <Drawer title={`运行日志 · ${task?.id.slice(0, 8) || ''}`} open={!!task} onClose={onClose} width={760}>
    <Timeline items={logs.map(log => ({ color: log.level === 'ERROR' ? 'red' : log.level === 'WARNING' ? 'orange' : 'green', children: <><Typography.Text code>{dayjs(log.created_at).format('HH:mm:ss')}</Typography.Text> {log.message}</> }))} />
    {!logs.length && <Typography.Text type="secondary">等待日志输出…</Typography.Text>}
  </Drawer>
}

function PlatformField({ platforms, form }: { platforms: Platform[]; form: ReturnType<typeof Form.useForm>[0] }) {
  const connected = platforms.filter(platform => platform.enabled && (platform.onboarding_status === 'active' || platform.active_items > 0))
  const options = platforms.map(platform => ({
    value: platform.id,
    label: `${platform.id}. ${platform.name || '国家数据局（参考来源）'} · ${platform.active_items.toLocaleString()} 条 · ${platform.onboarding_status}`,
    disabled: !connected.some(item => item.id === platform.id),
  }))
  return <Form.Item name="platform_ids" label="平台范围" extra="仅已接入且有公开数据的平台可选；不选择表示全部启用来源。">
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space>
        <Button size="small" onClick={() => form.setFieldValue('platform_ids', connected.map(item => item.id))}>选择全部已接入（{connected.length}）</Button>
        <Button size="small" onClick={() => form.setFieldValue('platform_ids', [])}>清空</Button>
      </Space>
      <Select mode="multiple" allowClear showSearch optionFilterProp="label" placeholder="批量选择已接入的数据交易所" options={options} style={{ width: '100%' }} maxTagCount="responsive" />
    </Space>
  </Form.Item>
}

export default function TasksPage({ user }: { user: User }) {
  const [triggerOpen, setTriggerOpen] = useState(false)
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [logTask, setLogTask] = useState<Task | null>(null)
  const [triggerForm] = Form.useForm()
  const [scheduleForm] = Form.useForm()
  const client = useQueryClient()
  const { message } = App.useApp()
  const tasks = useQuery<any>({ queryKey: ['tasks'], queryFn: () => api('/v1/tasks'), refetchInterval: 5000 })
  const runs = useQuery<any>({ queryKey: ['runs'], queryFn: () => api('/v1/runs'), refetchInterval: 10000 })
  const schedules = useQuery<any[]>({ queryKey: ['schedules'], queryFn: () => api('/v1/schedules') })
  const platforms = useQuery<Platform[]>({ queryKey: ['platforms'], queryFn: () => api('/v1/platforms') })
  const platformMap = useMemo(() => new Map((platforms.data || []).map(item => [item.id, item.name])), [platforms.data])

  const trigger = useMutation({ mutationFn: (body: any) => post<Task>('/v1/tasks', body), onSuccess: () => { message.success('任务已进入队列'); setTriggerOpen(false); triggerForm.resetFields(); client.invalidateQueries({ queryKey: ['tasks'] }) }, onError: (error: Error) => message.error(error.message || '任务创建失败') })
  const cancel = useMutation({ mutationFn: (id: string) => post(`/v1/tasks/${id}/cancel`), onSuccess: () => client.invalidateQueries({ queryKey: ['tasks'] }), onError: (error: Error) => message.error(error.message) })
  const retry = useMutation({ mutationFn: (id: string) => post(`/v1/tasks/${id}/retry`), onSuccess: () => client.invalidateQueries({ queryKey: ['tasks'] }), onError: (error: Error) => message.error(error.message) })
  const createSchedule = useMutation({ mutationFn: (body: any) => post('/v1/schedules', body), onSuccess: () => { message.success('计划已保存'); setScheduleOpen(false); scheduleForm.resetFields(); client.invalidateQueries({ queryKey: ['schedules'] }) }, onError: (error: Error) => message.error(error.message || '计划保存失败') })
  const deleteSchedule = useMutation({ mutationFn: (id: number) => remove(`/v1/schedules/${id}`), onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }), onError: (error: Error) => message.error(error.message) })

  const platformRange = (ids: number[]) => ids?.length ? <Typography.Text title={ids.map(id => platformMap.get(id) || id).join('、')}>{ids.length} 个已接入平台</Typography.Text> : '全部启用来源'
  const taskTable = <Table rowKey="id" dataSource={tasks.data?.items || []} pagination={false} loading={tasks.isLoading} columns={[
    { title: '任务 ID', dataIndex: 'id', render: (value: string) => <span className="mono table-link">{value.slice(0, 8)}</span> },
    { title: '模式', dataIndex: 'mode', render: (value: string) => <Tag color={value === 'full' ? 'blue' : 'green'}>{value === 'full' ? '全量采集' : '增量采集'}</Tag> },
    { title: '平台范围', dataIndex: 'platform_ids', render: platformRange },
    { title: '页数上限', dataIndex: 'max_pages', render: (value?: number) => value || '系统默认' },
    { title: '状态', dataIndex: 'status', render: state },
    { title: '发起人', dataIndex: 'requested_by' },
    { title: '创建时间', dataIndex: 'created_at', render: (value: string) => dayjs(value).format('MM-DD HH:mm:ss') },
    { title: '耗时', render: (_: unknown, row: Task) => row.started_at ? `${dayjs(row.finished_at || undefined).diff(dayjs(row.started_at), 'second')} 秒` : '—' },
    { title: '操作', render: (_: unknown, row: Task) => <Space><Button size="small" onClick={() => setLogTask(row)}>日志</Button><Button size="small" icon={<PauseCircleOutlined />} disabled={user.role !== 'admin' || !['queued', 'running'].includes(row.status)} onClick={() => cancel.mutate(row.id)}>取消</Button><Button size="small" icon={<RedoOutlined />} disabled={user.role !== 'admin' || !['failed', 'partial', 'cancelled'].includes(row.status)} onClick={() => retry.mutate(row.id)}>重跑</Button></Space> },
  ]} />
  const runTable = <Table rowKey="id" dataSource={runs.data?.items || []} loading={runs.isLoading} pagination={{ total: runs.data?.total, pageSize: 30 }} columns={[
    { title: '运行 ID', dataIndex: 'id', render: (value: string) => <span className="mono">{value.slice(0, 8)}</span> }, { title: '模式', dataIndex: 'mode' }, { title: '触发方式', dataIndex: 'trigger' }, { title: '状态', dataIndex: 'status', render: state }, { title: '开始', dataIndex: 'started_at', render: (value: string) => dayjs(value).format('MM-DD HH:mm:ss') }, { title: '结束', dataIndex: 'finished_at', render: (value: string) => value ? dayjs(value).format('MM-DD HH:mm:ss') : '—' }, { title: '统计', dataIndex: 'stats', render: (value: any) => `页面 ${value.pages || 0} · 条目 ${value.items_seen || 0} · 错误 ${value.errors || 0}` },
  ]} />
  const scheduleTable = <Table rowKey="id" dataSource={schedules.data || []} loading={schedules.isLoading} pagination={false} columns={[
    { title: '计划名称', dataIndex: 'name' }, { title: 'Cron', dataIndex: 'cron_expression', render: (value: string) => <Typography.Text code>{value}</Typography.Text> }, { title: '模式', dataIndex: 'mode', render: (value: string) => value === 'full' ? '全量' : '增量' }, { title: '平台范围', dataIndex: 'platform_ids', render: platformRange }, { title: '时区', dataIndex: 'timezone' }, { title: '下次执行', dataIndex: 'next_run_at', render: (value: string) => value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '—' }, { title: '启用', dataIndex: 'enabled', render: (value: boolean) => <Switch size="small" checked={value} disabled /> }, { title: '操作', render: (_: unknown, row: any) => <Button danger type="text" icon={<DeleteOutlined />} disabled={user.role !== 'admin'} onClick={() => Modal.confirm({ title: '删除计划？', onOk: () => deleteSchedule.mutate(row.id) })} /> },
  ]} />

  return <>
    <div className="page-head"><div><h1>任务中心</h1><p>批量选择已接入交易所，执行全量/增量采集并管理每日计划</p></div><Space><Button icon={<ScheduleOutlined />} disabled={user.role !== 'admin'} onClick={() => { scheduleForm.setFieldsValue({ cron_expression: '30 2 * * *', timezone: 'Asia/Shanghai', mode: 'incremental', enabled: true, platform_ids: [] }); setScheduleOpen(true) }}>新建计划</Button><Button type="primary" icon={<PlayCircleOutlined />} disabled={user.role !== 'admin'} onClick={() => { triggerForm.setFieldsValue({ mode: 'incremental', platform_ids: [] }); setTriggerOpen(true) }}>立即执行</Button></Space></div>
    <Card className="panel-card"><Tabs items={[{ key: 'tasks', label: '队列任务', children: taskTable }, { key: 'runs', label: '采集批次', children: runTable }, { key: 'schedules', label: '调度计划', children: scheduleTable }]} /></Card>
    <Modal title="立即执行采集" open={triggerOpen} onCancel={() => { setTriggerOpen(false); triggerForm.resetFields() }} onOk={() => triggerForm.submit()} confirmLoading={trigger.isPending} okText="创建任务"><Form form={triggerForm} layout="vertical" onFinish={values => trigger.mutate(values)}><Form.Item name="mode" label="运行模式" rules={[{ required: true }]}><Select options={[{ value: 'incremental', label: '增量采集（从第一页读取并在无变化后早停）' }, { value: 'full', label: '全量采集（完整分页/断点续采）' }]} /></Form.Item><PlatformField platforms={platforms.data || []} form={triggerForm} /><Form.Item name="max_pages" label="每个平台最多抓取页数" tooltip="留空使用系统默认值"><InputNumber min={1} style={{ width: '100%' }} placeholder="系统默认" /></Form.Item></Form></Modal>
    <Modal title="新建调度计划" open={scheduleOpen} onCancel={() => { setScheduleOpen(false); scheduleForm.resetFields() }} onOk={() => scheduleForm.submit()} okText="保存计划" confirmLoading={createSchedule.isPending} destroyOnClose><Form form={scheduleForm} layout="vertical" onFinish={values => createSchedule.mutate(values)}><Form.Item name="name" label="计划名称" rules={[{ required: true, message: '请输入计划名称' }, { max: 160, message: '计划名称不能超过 160 个字符' }]}><Input placeholder="例如：每日已接入平台增量采集" autoFocus /></Form.Item><Form.Item name="cron_expression" label="Cron 表达式" rules={[{ required: true, message: '请输入 Cron 表达式' }, { pattern: /^\S+(?:\s+\S+){4}$/, message: '请输入 5 段 Cron 表达式，例如 30 2 * * *' }]}><Input className="mono" placeholder="30 2 * * *" /></Form.Item><Space align="start"><Form.Item name="timezone" label="时区" rules={[{ required: true, message: '请输入时区' }]}><Input /></Form.Item><Form.Item name="mode" label="模式" rules={[{ required: true }]}><Select style={{ width: 140 }} options={[{ value: 'incremental', label: '增量' }, { value: 'full', label: '全量' }]} /></Form.Item><Form.Item name="enabled" label="启用" valuePropName="checked"><Switch /></Form.Item></Space><PlatformField platforms={platforms.data || []} form={scheduleForm} /><Form.Item name="max_pages" label="每个平台最多抓取页数"><InputNumber min={1} style={{ width: '100%' }} placeholder="系统默认" /></Form.Item></Form></Modal>
    <LogDrawer task={logTask} onClose={() => setLogTask(null)} />
  </>
}
