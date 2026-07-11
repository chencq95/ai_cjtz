import { PropsWithChildren } from 'react'
import { Alert, App, Avatar, Badge, Button, Dropdown, Form, Input, Layout, Menu, Modal, Space, Typography } from 'antd'
import {
  AlertOutlined, AuditOutlined, DatabaseOutlined, DashboardOutlined, LogoutOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, PartitionOutlined, SafetyCertificateOutlined,
  ScheduleOutlined, SettingOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, post } from '../api'
import type { User } from '../types'
import { useState } from 'react'

const { Header, Sider, Content } = Layout

export default function Shell({ user, onLogout, children }: PropsWithChildren<{ user: User; onLogout: () => void }>) {
  const [collapsed, setCollapsed] = useState(false)
  const [passwordOpen, setPasswordOpen] = useState(Boolean(user.must_change_password))
  const [passwordForm] = Form.useForm()
  const location = useLocation()
  const navigate = useNavigate()
  const { message } = App.useApp()
  const alerts = useQuery<any[]>({ queryKey: ['alerts'], queryFn: () => api('/v1/alerts'), refetchInterval: 30_000 })
  const logout = useMutation({
    mutationFn: () => post('/v1/auth/logout'),
    onSuccess: () => { onLogout(); navigate('/') },
    onError: (error: Error) => message.error(error.message),
  })
  const changePassword = useMutation({
    mutationFn: (values: { current_password: string; new_password: string }) => post('/v1/auth/change-password', values),
    onSuccess: () => { message.success('密码已更新'); setPasswordOpen(false); passwordForm.resetFields(); onLogout() },
    onError: (error: Error) => message.error(error.message),
  })
  const items = [
    { key: '/', icon: <DashboardOutlined />, label: '运行总览' },
    { key: '/platforms', icon: <PartitionOutlined />, label: '平台管理' },
    { key: '/tasks', icon: <ScheduleOutlined />, label: '任务中心' },
    { key: '/catalog', icon: <DatabaseOutlined />, label: '数据目录' },
    { key: '/coverage', icon: <SafetyCertificateOutlined />, label: '覆盖率矩阵' },
    { key: '/reviews', icon: <AuditOutlined />, label: '数据审核' },
    { key: '/administration', icon: <SettingOutlined />, label: '用户与审计' },
  ]
  return (
    <Layout className="app-layout">
      <Sider trigger={null} collapsible collapsed={collapsed} width={232} className="app-sider">
        <div className="brand">
          <div className="brand-mark">数</div>
          {!collapsed && <div><strong>数据场景探针</strong><span>全国交易所运维平台</span></div>}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} onClick={({ key }) => navigate(key)} />
        {!collapsed && <div className="sider-foot"><span className="live-dot" />管理服务已连接</div>}
      </Sider>
      <Layout>
        <Header className="app-header">
          <Button type="text" icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} onClick={() => setCollapsed(!collapsed)} />
          <Space size="large">
            <Badge count={alerts.data?.length || 0} size="small"><AlertOutlined className="header-icon" /></Badge>
            <Dropdown menu={{ items: [
              { key: 'role', label: user.role === 'admin' ? '管理员' : '只读用户', disabled: true },
              { key: 'password', label: '修改密码', onClick: () => setPasswordOpen(true) },
              { type: 'divider' },
              { key: 'logout', icon: <LogoutOutlined />, label: '退出登录', onClick: () => logout.mutate() },
            ] }}>
              <Space className="user-chip"><Avatar>{user.username.slice(0, 1).toUpperCase()}</Avatar><Typography.Text>{user.username}</Typography.Text></Space>
            </Dropdown>
          </Space>
        </Header>
        <Content className="app-content">
          {window.location.protocol === 'http:' && window.location.hostname !== '127.0.0.1' && window.location.hostname !== 'localhost' && (
            <Alert banner showIcon type="warning" message="当前使用 HTTP 临时访问，登录流量未加密；请尽快配置域名 HTTPS。" />
          )}
          {children}
        </Content>
      </Layout>
      <Modal title="修改登录密码" open={passwordOpen} closable={!user.must_change_password} maskClosable={false} onCancel={() => !user.must_change_password && setPasswordOpen(false)} onOk={() => passwordForm.validateFields().then(values => changePassword.mutate(values))} confirmLoading={changePassword.isPending}>
        <Form form={passwordForm} layout="vertical"><Form.Item name="current_password" label="当前密码" rules={[{required:true}]}><Input.Password autoComplete="current-password" /></Form.Item><Form.Item name="new_password" label="新密码" rules={[{required:true,min:10}]}><Input.Password autoComplete="new-password" /></Form.Item><Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[{required:true},{validator:(_,value)=>value===passwordForm.getFieldValue('new_password')?Promise.resolve():Promise.reject(new Error('两次密码不一致'))}]}><Input.Password autoComplete="new-password" /></Form.Item></Form>
      </Modal>
    </Layout>
  )
}
