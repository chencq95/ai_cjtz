import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Spin } from 'antd'
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { api } from './api'
import type { User } from './types'
import Shell from './components/Shell'
import LoginPage from './pages/LoginPage'
const DashboardPage = lazy(() => import('./pages/DashboardPage'))
const PlatformsPage = lazy(() => import('./pages/PlatformsPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const CatalogPage = lazy(() => import('./pages/CatalogPage'))
const CoveragePage = lazy(() => import('./pages/CoveragePage'))
const ReviewsPage = lazy(() => import('./pages/ReviewsPage'))
const AdministrationPage = lazy(() => import('./pages/AdministrationPage'))

export default function App() {
  const client = useQueryClient()
  const me = useQuery<User>({ queryKey: ['me'], queryFn: () => api('/v1/auth/me'), retry: false })

  if (me.isLoading) return <div className="boot"><Spin size="large" /><span>正在连接运维平台</span></div>
  if (me.isError) return <LoginPage onLogin={() => client.invalidateQueries({ queryKey: ['me'] })} />
  if (!me.data) return <Alert type="error" message="无法读取当前用户" />

  return (
    <Shell user={me.data} onLogout={() => client.invalidateQueries({ queryKey: ['me'] })}>
      <Suspense fallback={<div className="boot"><Spin size="large" /></div>}><Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/platforms" element={<PlatformsPage user={me.data} />} />
        <Route path="/tasks" element={<TasksPage user={me.data} />} />
        <Route path="/catalog" element={<CatalogPage />} />
        <Route path="/coverage" element={<CoveragePage />} />
        <Route path="/reviews" element={<ReviewsPage user={me.data} />} />
        <Route path="/administration" element={<AdministrationPage user={me.data} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes></Suspense>
    </Shell>
  )
}
