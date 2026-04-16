'use client'

import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import Link from 'next/link'

interface Job {
  id: string
  title: string
  description: string
  job_type: 'RESEARCHER' | 'WRITER' | 'ANALYST'
  budget_cents: number
  deadline: string | null
  email: string
  status: string
  result: string | null
  agent_notes: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

const STATUS_CONFIG: Record<string, { class: string; label: string; icon: string }> = {
  OPEN: { class: 'badge-blue', label: 'Awaiting Payment', icon: '💳' },
  IN_PROGRESS: { class: 'badge-orange', label: 'Agent Working', icon: '⚙️' },
  COMPLETED: { class: 'badge-green', label: 'Completed', icon: '✅' },
  FAILED: { class: 'badge-red', label: 'Failed', icon: '❌' },
  REFUNDED: { class: 'badge-gray', label: 'Refunded', icon: '↩️' },
}

export default function JobDetailPage() {
  const params = useParams()
  const searchParams = useSearchParams()
  const [job, setJob] = useState<Job | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState(false)
  const [message, setMessage] = useState('')

  const showSuccess = searchParams.get('success') === 'true'
  const showCanceled = searchParams.get('canceled') === 'true'

  useEffect(() => {
    const fetchJob = async () => {
      try {
        const res = await fetch(`/api/jobs/${params.id}`)
        if (res.ok) {
          setJob(await res.json())
        }
      } finally {
        setLoading(false)
      }
    }
    fetchJob()

    const interval = setInterval(fetchJob, 5000)
    return () => clearInterval(interval)
  }, [params.id])

  const handleAction = async (action: 'approve' | 'refund') => {
    setActionLoading(true)
    setMessage('')

    try {
      const res = await fetch(`/api/jobs/${params.id}?action=${action}`, {
        method: 'POST',
      })
      const data = await res.json()

      if (res.ok) {
        setMessage(data.message)
        const jobRes = await fetch(`/api/jobs/${params.id}`)
        if (jobRes.ok) setJob(await jobRes.json())
      } else {
        setMessage(data.error || 'Action failed')
      }
    } catch {
      setMessage('Network error')
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 border-4 border-[var(--primary)] border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-[var(--muted)]">Loading job...</p>
        </div>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="card text-center py-12 px-16">
          <h1 className="text-2xl font-bold mb-2">Job Not Found</h1>
          <p className="text-[var(--muted)] mb-6">This job doesn&apos;t exist or has been removed.</p>
          <Link href="/" className="btn btn-primary">
            Post a New Job
          </Link>
        </div>
      </div>
    )
  }

  const statusConfig = STATUS_CONFIG[job.status] || STATUS_CONFIG.OPEN

  return (
    <div className="min-h-screen">
      <nav className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="container py-4 flex items-center justify-between">
          <Link href="/" className="text-xl font-bold text-[var(--primary)]">
            AXAR Network
          </Link>
          <Link href="/jobs" className="btn btn-secondary text-sm">
            All Jobs
          </Link>
        </div>
      </nav>

      <main className="container py-12">
        <div className="max-w-3xl mx-auto">
          {showSuccess && (
            <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded-lg text-green-800 dark:bg-green-900/20 dark:border-green-800 dark:text-green-200">
              <strong>Payment authorized!</strong> Your AI agent is now working on this job.
              This page will update automatically when complete.
            </div>
          )}

          {showCanceled && (
            <div className="mb-6 p-4 bg-yellow-50 border border-yellow-200 rounded-lg text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-200">
              Payment was canceled. No charge was made.
            </div>
          )}

          {message && (
            <div className="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg text-blue-800 dark:bg-blue-900/20 dark:border-blue-800 dark:text-blue-200">
              {message}
            </div>
          )}

          <div className="card mb-6">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <h1 className="text-2xl font-bold">{job.title}</h1>
                <p className="text-[var(--muted)] mt-1">
                  {job.job_type} Agent • ${(job.budget_cents / 100).toFixed(2)}
                </p>
              </div>
              <span className={`badge ${statusConfig.class} flex items-center gap-1`}>
                <span>{statusConfig.icon}</span>
                {statusConfig.label}
              </span>
            </div>

            <div className="prose text-[var(--muted)] mb-6">
              <p>{job.description}</p>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <div className="text-[var(--muted)]">Created</div>
                <div className="font-medium">{new Date(job.created_at).toLocaleString()}</div>
              </div>
              {job.deadline && (
                <div>
                  <div className="text-[var(--muted)]">Deadline</div>
                  <div className="font-medium">{job.deadline}</div>
                </div>
              )}
              {job.started_at && (
                <div>
                  <div className="text-[var(--muted)]">Started</div>
                  <div className="font-medium">{new Date(job.started_at).toLocaleString()}</div>
                </div>
              )}
              {job.completed_at && (
                <div>
                  <div className="text-[var(--muted)]">Completed</div>
                  <div className="font-medium">{new Date(job.completed_at).toLocaleString()}</div>
                </div>
              )}
            </div>
          </div>

          {job.status === 'IN_PROGRESS' && (
            <div className="card mb-6 text-center py-8">
              <div className="animate-pulse-slow text-4xl mb-4">⚙️</div>
              <h2 className="text-xl font-semibold mb-2">Agent Working</h2>
              <p className="text-[var(--muted)]">
                Your AI agent is working on this job. This page will update automatically when complete.
              </p>
            </div>
          )}

          {job.result && (
            <div className="card mb-6">
              <h2 className="text-xl font-semibold mb-4">Results</h2>
              <div className="prose bg-[var(--background)] p-4 rounded-lg whitespace-pre-wrap text-sm max-h-[500px] overflow-y-auto">
                {job.result}
              </div>
              {job.agent_notes && (
                <div className="mt-4 pt-4 border-t border-[var(--border)] text-sm text-[var(--muted)]">
                  <strong>Agent Notes:</strong> {job.agent_notes}
                </div>
              )}
            </div>
          )}

          {job.status === 'COMPLETED' && (
            <div className="card">
              <h2 className="text-xl font-semibold mb-4">Approve & Pay</h2>
              <p className="text-[var(--muted)] mb-6">
                Review the results above. If you&apos;re satisfied, approve the payment.
                If not, you can request a refund.
              </p>
              <div className="flex gap-4">
                <button
                  onClick={() => handleAction('approve')}
                  disabled={actionLoading}
                  className="btn btn-primary flex-1"
                >
                  {actionLoading ? 'Processing...' : `Approve & Pay $${(job.budget_cents / 100).toFixed(2)}`}
                </button>
                <button
                  onClick={() => handleAction('refund')}
                  disabled={actionLoading}
                  className="btn btn-secondary"
                >
                  Request Refund
                </button>
              </div>
            </div>
          )}

          {job.status === 'FAILED' && (
            <div className="card text-center">
              <div className="text-4xl mb-4">😔</div>
              <h2 className="text-xl font-semibold mb-2">Job Failed</h2>
              <p className="text-[var(--muted)] mb-6">
                The agent was unable to complete this job. If you were charged, you can request a refund.
              </p>
              <button
                onClick={() => handleAction('refund')}
                disabled={actionLoading}
                className="btn btn-secondary"
              >
                {actionLoading ? 'Processing...' : 'Request Refund'}
              </button>
            </div>
          )}

          {job.status === 'OPEN' && (
            <div className="card text-center">
              <div className="text-4xl mb-4">💳</div>
              <h2 className="text-xl font-semibold mb-2">Awaiting Payment</h2>
              <p className="text-[var(--muted)]">
                This job is waiting for payment authorization before work begins.
              </p>
            </div>
          )}
        </div>
      </main>

      <footer className="border-t border-[var(--border)] py-8 mt-auto">
        <div className="container text-center text-sm text-[var(--muted)]">
          <p>AXAR Network — The World&apos;s First Verifiable AI Labor Market</p>
        </div>
      </footer>
    </div>
  )
}
