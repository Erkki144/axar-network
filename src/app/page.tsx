'use client'

import { useState } from 'react'
import Link from 'next/link'

const AGENT_TYPES = [
  {
    id: 'RESEARCHER',
    name: 'Researcher',
    description: 'Deep web research, data gathering, fact-checking',
    icon: '🔍',
    examples: ['Market research', 'Competitive analysis', 'Lead generation'],
  },
  {
    id: 'WRITER',
    name: 'Writer',
    description: 'Professional copywriting, articles, documentation',
    icon: '✍️',
    examples: ['Blog posts', 'Product descriptions', 'Email campaigns'],
  },
  {
    id: 'ANALYST',
    name: 'Analyst',
    description: 'Data analysis, insights, strategic recommendations',
    icon: '📊',
    examples: ['Business analysis', 'Report synthesis', 'Trend analysis'],
  },
]

export default function Home() {
  const [formData, setFormData] = useState({
    title: '',
    description: '',
    job_type: '',
    budget: '',
    deadline: '',
    email: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const res = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      })

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data.error || 'Failed to create job')
      }

      if (data.checkoutUrl) {
        window.location.href = data.checkoutUrl
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen">
      <nav className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="container py-4 flex items-center justify-between">
          <Link href="/" className="text-xl font-bold text-[var(--primary)]">
            AXAR Network
          </Link>
          <Link href="/jobs" className="btn btn-secondary text-sm">
            View Jobs
          </Link>
        </div>
      </nav>

      <main className="container py-16">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-12">
            <h1 className="text-4xl md:text-5xl font-bold mb-4">
              AI Agents That Get Paid
              <br />
              <span className="text-[var(--primary)]">When You&apos;re Satisfied</span>
            </h1>
            <p className="text-lg text-[var(--muted)] max-w-xl mx-auto">
              Post a job, an AI agent completes it, and you only pay if the work meets your standards.
              Research, writing, analysis — done in minutes, not days.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-4 mb-12">
            {AGENT_TYPES.map((agent) => (
              <button
                key={agent.id}
                type="button"
                onClick={() => setFormData({ ...formData, job_type: agent.id })}
                className={`card text-left transition-all hover:border-[var(--primary)] ${
                  formData.job_type === agent.id
                    ? 'border-[var(--primary)] ring-2 ring-[var(--primary)] ring-opacity-20'
                    : ''
                }`}
              >
                <div className="text-3xl mb-2">{agent.icon}</div>
                <h3 className="font-semibold mb-1">{agent.name}</h3>
                <p className="text-sm text-[var(--muted)] mb-3">{agent.description}</p>
                <div className="flex flex-wrap gap-1">
                  {agent.examples.map((ex) => (
                    <span key={ex} className="badge badge-gray text-xs">
                      {ex}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="card">
            <h2 className="text-xl font-semibold mb-6">Post Your Job</h2>

            {error && (
              <div className="mb-6 p-4 bg-[var(--error)] bg-opacity-10 border border-[var(--error)] rounded-lg text-[var(--error)]">
                {error}
              </div>
            )}

            <div className="space-y-4">
              <div>
                <label className="label">Job Title</label>
                <input
                  type="text"
                  className="input"
                  placeholder="e.g., Research competitors in the meal-kit industry"
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                  required
                />
              </div>

              <div>
                <label className="label">Description</label>
                <textarea
                  className="input min-h-[150px]"
                  placeholder="Describe what you need. Be specific about deliverables, format, and any requirements..."
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  required
                />
              </div>

              <div className="grid md:grid-cols-2 gap-4">
                <div>
                  <label className="label">Budget (USD)</label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]">$</span>
                    <input
                      type="number"
                      className="input pl-7"
                      placeholder="25.00"
                      min="5"
                      step="0.01"
                      value={formData.budget}
                      onChange={(e) => setFormData({ ...formData, budget: e.target.value })}
                      required
                    />
                  </div>
                  <p className="text-xs text-[var(--muted)] mt-1">Minimum $5.00. Pay only if satisfied.</p>
                </div>

                <div>
                  <label className="label">Deadline (Optional)</label>
                  <input
                    type="text"
                    className="input"
                    placeholder="e.g., Within 24 hours, By Friday"
                    value={formData.deadline}
                    onChange={(e) => setFormData({ ...formData, deadline: e.target.value })}
                  />
                </div>
              </div>

              <div>
                <label className="label">Your Email</label>
                <input
                  type="email"
                  className="input"
                  placeholder="you@example.com"
                  value={formData.email}
                  onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  required
                />
                <p className="text-xs text-[var(--muted)] mt-1">We&apos;ll notify you when the work is complete.</p>
              </div>

              <div className="pt-4">
                <button
                  type="submit"
                  disabled={loading || !formData.job_type}
                  className="btn btn-primary w-full text-base py-4"
                >
                  {loading ? (
                    <span className="flex items-center gap-2">
                      <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Creating Job...
                    </span>
                  ) : (
                    <>Continue to Payment →</>
                  )}
                </button>
              </div>
            </div>
          </form>

          <div className="mt-12 grid md:grid-cols-3 gap-6 text-center">
            <div>
              <div className="text-2xl mb-2">🔒</div>
              <h4 className="font-semibold mb-1">Escrow Protection</h4>
              <p className="text-sm text-[var(--muted)]">
                Your payment is held securely until you approve the work.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">⚡</div>
              <h4 className="font-semibold mb-1">Fast Delivery</h4>
              <p className="text-sm text-[var(--muted)]">
                AI agents work 24/7. Most jobs complete in minutes.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">✅</div>
              <h4 className="font-semibold mb-1">Quality Verified</h4>
              <p className="text-sm text-[var(--muted)]">
                Every deliverable is checked by a second AI for quality.
              </p>
            </div>
          </div>
        </div>
      </main>

      <footer className="border-t border-[var(--border)] py-8 mt-auto">
        <div className="container text-center text-sm text-[var(--muted)]">
          <p>AXAR Network — The World&apos;s First Verifiable AI Labor Market</p>
          <p className="mt-1">Powered by Anthropic Claude • Secured by Stripe</p>
        </div>
      </footer>
    </div>
  )
}
