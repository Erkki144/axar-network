'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'

interface Job {
  id: string
  title: string
  description: string
  job_type: 'RESEARCHER' | 'WRITER' | 'ANALYST'
  budget_cents: number
  status: string
  created_at: string
}

const STATUS_BADGES: Record<string, { class: string; label: string }> = {
  OPEN: { class: 'badge-blue', label: 'Awaiting Payment' },
  IN_PROGRESS: { class: 'badge-orange', label: 'In Progress' },
  COMPLETED: { class: 'badge-green', label: 'Completed' },
  FAILED: { class: 'badge-red', label: 'Failed' },
  REFUNDED: { class: 'badge-gray', label: 'Refunded' },
}

const TYPE_ICONS = {
  RESEARCHER: '🔍',
  WRITER: '✍️',
  ANALYST: '📊',
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/jobs')
      .then((res) => res.json())
      .then((data) => {
        setJobs(data)
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  return (
    <div className="min-h-screen">
      <nav className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="container py-4 flex items-center justify-between">
          <Link href="/" className="text-xl font-bold text-[var(--primary)]">
            AXAR Network
          </Link>
          <Link href="/" className="btn btn-primary text-sm">
            Post a Job
          </Link>
        </div>
      </nav>

      <main className="container py-12">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-3xl font-bold mb-2">Recent Jobs</h1>
          <p className="text-[var(--muted)] mb-8">
            Browse completed and in-progress jobs on the AXAR Network.
          </p>

          {loading ? (
            <div className="text-center py-12">
              <div className="animate-spin h-8 w-8 border-4 border-[var(--primary)] border-t-transparent rounded-full mx-auto mb-4" />
              <p className="text-[var(--muted)]">Loading jobs...</p>
            </div>
          ) : jobs.length === 0 ? (
            <div className="card text-center py-12">
              <p className="text-lg text-[var(--muted)] mb-4">No jobs yet</p>
              <Link href="/" className="btn btn-primary">
                Post the First Job
              </Link>
            </div>
          ) : (
            <div className="space-y-4">
              {jobs.map((job) => {
                const statusInfo = STATUS_BADGES[job.status] || STATUS_BADGES.OPEN
                return (
                  <Link
                    key={job.id}
                    href={`/jobs/${job.id}`}
                    className="card block hover:border-[var(--primary)] transition-colors"
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-xl">{TYPE_ICONS[job.job_type]}</span>
                          <h2 className="text-lg font-semibold truncate">{job.title}</h2>
                        </div>
                        <p className="text-sm text-[var(--muted)] line-clamp-2 mb-3">
                          {job.description}
                        </p>
                        <div className="flex items-center gap-4 text-sm">
                          <span className="font-medium">
                            ${(job.budget_cents / 100).toFixed(2)}
                          </span>
                          <span className="text-[var(--muted)]">
                            {new Date(job.created_at).toLocaleDateString()}
                          </span>
                        </div>
                      </div>
                      <div className="flex-shrink-0">
                        <span className={`badge ${statusInfo.class}`}>
                          {statusInfo.label}
                        </span>
                      </div>
                    </div>
                  </Link>
                )
              })}
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
