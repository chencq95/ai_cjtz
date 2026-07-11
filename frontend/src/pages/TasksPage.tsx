import { App, Button, Card, Drawer, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, Timeline, Typography } from 'antd'
import { DeleteOutlined, PauseCircleOutlined, PlayCircleOutlined, RedoOutlined, ScheduleOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import dayjs from 'dayjs'
import { api, patch, post, remove } from '../api'
import type { Platform, Task, User } from '../types'

const state = (value:string) => <span className={`status-pill status-${value}`}>{value}</span>

function LogDrawer({ task, onClose }: { task: Task | null; onClose: () => void }) {
  const [logs, setLogs] = useState<Array<{id:string;level:string;message:string;created_at:string}>>([])
  useEffect(() => {
    if (!task) return
    setLogs([])
    const stream = new EventSource(`/api/v1/tasks/${task.id}/logs`, { withCredentials: true })
    stream.addEventListener('log', event => { const data = JSON.parse((event as MessageEvent).data); setLogs(old => [...old, { id: (event as MessageEvent).lastEventId, ...data }]) })
    stream.addEventListener('end', () => stream.close())
    return () => stream.close()
  }, [task])
  return <Drawer title={`运行日志 · ${task?.id.slice(0,8) || ''}`} open={!!task} onClose={onClose} width={760}><Timeline items={logs.map(log => ({ color: log.level === 'ERROR' ? 'red' : log.level === 'WARNING' ? 'orange' : 'green', children: <><Typography.Text code>{dayjs(log.created_at).format('HH:mm:ss')}</Typography.Text> {log.message}</> }))} />{!logs.length && <Typography.Text type="secondary">等待日志输出…</Typography.Text>}</Drawer>
}

export default function TasksPage({ user }: { user: User }) {
  const [triggerOpen,setTriggerOpen] = useState(false)
  const [scheduleOpen,setScheduleOpen] = useState(false)
  const [logTask,setLogTask] = useState<Task|null>(null)
  const [triggerForm] = Form.useForm()
  const [scheduleForm] = Form.useForm()
  const client = useQueryClient(); const { message } = App.useApp()
  const tasks = useQuery<any>({ queryKey:['tasks'], queryFn:()=>api('/v1/tasks'), refetchInterval:5000 })
  const runs = useQuery<any>({ queryKey:['runs'], queryFn:()=>api('/v1/runs'), refetchInterval:10000 })
  const schedules = useQuery<any[]>({ queryKey:['schedules'], queryFn:()=>api('/v1/schedules') })
  const platforms = useQuery<Platform[]>({ queryKey:['platforms'], queryFn:()=>api('/v1/platforms') })
  const trigger = useMutation({ mutationFn:(body:any)=>post<Task>('/v1/tasks',body), onSuccess:()=>{message.success('任务已进入队列');setTriggerOpen(false);client.invalidateQueries({queryKey:['tasks']})},onError:(e:Error)=>message.error(e.message) })
  const cancel = useMutation({ mutationFn:(id:string)=>post(`/v1/tasks/${id}/cancel`), onSuccess:()=>client.invalidateQueries({queryKey:['tasks']}),onError:(e:Error)=>message.error(e.message) })
  const retry = useMutation({ mutationFn:(id:string)=>post(`/v1/tasks/${id}/retry`), onSuccess:()=>client.invalidateQueries({queryKey:['tasks']}),onError:(e:Error)=>message.error(e.message) })
  const createSchedule = useMutation({ mutationFn:(body:any)=>post('/v1/schedules',body), onSuccess:()=>{message.success('计划已保存');setScheduleOpen(false);client.invalidateQueries({queryKey:['schedules']})},onError:(e:Error)=>message.error(e.message) })
  const deleteSchedule = useMutation({ mutationFn:(id:number)=>remove(`/v1/schedules/${id}`),onSuccess:()=>client.invalidateQueries({queryKey:['schedules']}),onError:(e:Error)=>message.error(e.message) })
  const taskTable = <Table rowKey="id" dataSource={tasks.data?.items || []} pagination={false} loading={tasks.isLoading} columns={[
    {title:'任务 ID',dataIndex:'id',render:(v:string)=><span className="mono table-link">{v.slice(0,8)}</span>},{title:'模式',dataIndex:'mode',render:(v:string)=><Tag>{v==='full'?'完整校准':'增量'}</Tag>},{title:'平台范围',dataIndex:'platform_ids',render:(v:number[])=>v.length?`${v.length} 个指定平台`:'全部平台'},{title:'页数上限',dataIndex:'max_pages',render:(v?:number)=>v||'系统默认'},{title:'状态',dataIndex:'status',render:state},{title:'发起人',dataIndex:'requested_by'},{title:'创建时间',dataIndex:'created_at',render:(v:string)=>dayjs(v).format('MM-DD HH:mm:ss')},{title:'耗时',render:(_:unknown,r:Task)=>r.started_at?`${dayjs(r.finished_at || undefined).diff(dayjs(r.started_at),'second')} 秒`:'—'},{title:'操作',render:(_:unknown,r:Task)=><Space><Button size="small" onClick={()=>setLogTask(r)}>日志</Button><Button size="small" icon={<PauseCircleOutlined/>} disabled={user.role!=='admin'||!['queued','running'].includes(r.status)} onClick={()=>cancel.mutate(r.id)}>取消</Button><Button size="small" icon={<RedoOutlined/>} disabled={user.role!=='admin'||!['failed','partial','cancelled'].includes(r.status)} onClick={()=>retry.mutate(r.id)}>重跑</Button></Space>},
  ]} />
  const runTable = <Table rowKey="id" dataSource={runs.data?.items || []} loading={runs.isLoading} pagination={{total:runs.data?.total,pageSize:30}} columns={[{title:'运行 ID',dataIndex:'id',render:(v:string)=><span className="mono">{v.slice(0,8)}</span>},{title:'模式',dataIndex:'mode'},{title:'触发方式',dataIndex:'trigger'},{title:'状态',dataIndex:'status',render:state},{title:'开始',dataIndex:'started_at',render:(v:string)=>dayjs(v).format('MM-DD HH:mm:ss')},{title:'结束',dataIndex:'finished_at',render:(v:string)=>v?dayjs(v).format('MM-DD HH:mm:ss'):'—'},{title:'统计',dataIndex:'stats',render:(v:any)=>`页面 ${v.pages||0} · 条目 ${v.items_seen||0} · 错误 ${v.errors||0}`}]} />
  const scheduleTable = <Table rowKey="id" dataSource={schedules.data || []} loading={schedules.isLoading} pagination={false} columns={[{title:'计划名称',dataIndex:'name'},{title:'Cron',dataIndex:'cron_expression',render:(v:string)=><Typography.Text code>{v}</Typography.Text>},{title:'模式',dataIndex:'mode'},{title:'时区',dataIndex:'timezone'},{title:'下次执行',dataIndex:'next_run_at',render:(v:string)=>dayjs(v).format('YYYY-MM-DD HH:mm')},{title:'启用',dataIndex:'enabled',render:(v:boolean)=><Switch size="small" checked={v} disabled/>},{title:'操作',render:(_:unknown,r:any)=><Button danger type="text" icon={<DeleteOutlined/>} disabled={user.role!=='admin'} onClick={()=>Modal.confirm({title:'删除计划？',onOk:()=>deleteSchedule.mutate(r.id)})}/>}]} />
  return <>
    <div className="page-head"><div><h1>任务中心</h1><p>计划调度、手动运行、失败重试与实时日志</p></div><Space><Button icon={<ScheduleOutlined/>} disabled={user.role!=='admin'} onClick={()=>{scheduleForm.setFieldsValue({cron_expression:'30 2 * * *',timezone:'Asia/Shanghai',mode:'incremental',enabled:true,platform_ids:[]});setScheduleOpen(true)}}>新建计划</Button><Button type="primary" icon={<PlayCircleOutlined/>} disabled={user.role!=='admin'} onClick={()=>{triggerForm.setFieldsValue({mode:'incremental',platform_ids:[]});setTriggerOpen(true)}}>立即执行</Button></Space></div>
    <Card className="panel-card"><Tabs items={[{key:'tasks',label:'队列任务',children:taskTable},{key:'runs',label:'采集批次',children:runTable},{key:'schedules',label:'调度计划',children:scheduleTable}]} /></Card>
    <Modal title="立即执行采集" open={triggerOpen} onCancel={()=>setTriggerOpen(false)} onOk={()=>triggerForm.validateFields().then(v=>trigger.mutate(v))} confirmLoading={trigger.isPending}><Form form={triggerForm} layout="vertical"><Form.Item name="mode" label="运行模式"><Select options={[{value:'incremental',label:'增量采集'},{value:'full',label:'完整校准'}]}/></Form.Item><Form.Item name="platform_ids" label="平台范围"><Select mode="multiple" allowClear placeholder="不选择表示全部平台" options={(platforms.data||[]).map(p=>({value:p.id,label:`${p.id}. ${p.name}`}))}/></Form.Item><Form.Item name="max_pages" label="每个平台最多抓取页数" tooltip="留空使用系统默认值"><InputNumber min={1} style={{width:'100%'}} placeholder="系统默认"/></Form.Item></Form></Modal>
    <Modal title="新建调度计划" open={scheduleOpen} onCancel={()=>setScheduleOpen(false)} onOk={()=>scheduleForm.validateFields().then(v=>createSchedule.mutate(v))} confirmLoading={createSchedule.isPending}><Form form={scheduleForm} layout="vertical"><Form.Item name="name" label="计划名称" rules={[{required:true}]}><Input/></Form.Item><Form.Item name="cron_expression" label="Cron 表达式" rules={[{required:true}]}><Input className="mono"/></Form.Item><Space><Form.Item name="timezone" label="时区"><Input/></Form.Item><Form.Item name="mode" label="模式"><Select style={{width:140}} options={[{value:'incremental',label:'增量'},{value:'full',label:'完整'}]}/></Form.Item><Form.Item name="enabled" label="启用" valuePropName="checked"><Switch/></Form.Item></Space><Form.Item name="platform_ids" label="平台范围"><Select mode="multiple" options={(platforms.data||[]).map(p=>({value:p.id,label:p.name}))}/></Form.Item><Form.Item name="max_pages" label="每个平台最多抓取页数"><InputNumber min={1} style={{width:'100%'}} placeholder="系统默认"/></Form.Item></Form></Modal>
    <LogDrawer task={logTask} onClose={()=>setLogTask(null)}/>
  </>
}
