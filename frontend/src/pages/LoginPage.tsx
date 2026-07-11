import { App, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { useMutation } from '@tanstack/react-query'
import { post } from '../api'

export default function LoginPage({ onLogin }: { onLogin: () => void }) {
  const { message } = App.useApp()
  const login = useMutation({
    mutationFn: (values: { username: string; password: string }) => post('/v1/auth/login', values),
    onSuccess: () => onLogin(),
    onError: (error: Error) => message.error(error.message),
  })
  return (
    <div className="login-page">
      <div className="login-orb login-orb-a" /><div className="login-orb login-orb-b" />
      <section className="login-story">
        <div className="eyebrow">NATIONAL DATA EXCHANGE OBSERVATORY</div>
        <h1>看见数据要素市场<br />每一次真实变化</h1>
        <p>统一采集、版本追踪、覆盖审计与质量复核，为全国数据交易平台建立可追溯的公共目录。</p>
        <div className="story-stats"><div><b>38</b><span>来源建档</span></div><div><b>24h</b><span>增量周期</span></div><div><b>100%</b><span>变更留痕</span></div></div>
      </section>
      <Card className="login-card" bordered={false}>
        <div className="login-logo">数</div>
        <Typography.Title level={2}>欢迎回来</Typography.Title>
        <Typography.Paragraph type="secondary">登录全国数据交易所爬虫运维平台</Typography.Paragraph>
        <Form layout="vertical" size="large" onFinish={values => login.mutate(values)} initialValues={{ username: 'admin' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}><Input prefix={<UserOutlined />} autoComplete="username" /></Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}><Input.Password prefix={<LockOutlined />} autoComplete="current-password" /></Form.Item>
          <Button block type="primary" htmlType="submit" loading={login.isPending}>进入运维平台</Button>
        </Form>
        <div className="login-hint">首次登录后请立即修改初始化密码</div>
      </Card>
    </div>
  )
}
