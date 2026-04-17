'use client'

import { useState } from 'react'
import Link from 'next/link'

const AGENT_TYPES = [
  {
    id: 'RESEARCHER',
    name: 'Competitor Research',
    description: 'Deep competitor analysis with real data',
    icon: '🔍',
    examples: ['Competitor teardowns', 'Market positioning', 'Content gap analysis'],
    price: 'From $15',
  },
  {
    id: 'WRITER',
    name: 'SEO Content',
    description: 'Blog posts, social media, email copy',
    icon: '✍️',
    examples: ['SEO blog posts', 'LinkedIn content', 'Email sequences'],
    price: 'From $10',
  },
  {
    id: 'ANALYST',
    name: 'Content Strategy',
    description: 'Data-driven content recommendations',
    icon: '📊',
    examples: ['Content audits', 'Editorial calendars', 'Performance analysis'],
    price: 'From $20',
  },
]

const EXAMPLE_WORK = [
  {
    title: 'Competitor Analysis: Bolt Food vs Wolt',
    type: 'RESEARCHER',
    preview: 'Full competitive teardown including market share, pricing strategy, app features, and content strategy across 3 markets...',
    deliverable: '12-page report with 8 actionable recommendations',
    time: '4 minutes',
  },
  {
    title: 'SEO Blog Post: "10 Food Delivery Trends in 2026"',
    type: 'WRITER',
    preview: '2,400-word SEO-optimized article targeting "food delivery trends" keyword with internal linking structure...',
    deliverable: 'Publish-ready article + meta tags + social snippets',
    time: '3 minutes',
  },
  {
    title: 'Q2 Content Strategy for SaaS Startup',
    type: 'ANALYST',
    preview: 'Based on competitor content analysis, identified 14 content gaps and created 12-week editorial calendar...',
    deliverable: 'Strategy doc + content calendar + topic clusters',
    time: '6 minutes',
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
  const [demoData, setDemoData] = useState({ url: '', email: '' })
  const [demoResult, setDemoResult] = useState<string | null>(null)
  const [demoLoading, setDemoLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [activeTab, setActiveTab] = useState<'demo' | 'full'>('demo')

  const handleDemoSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setDemoLoading(true)
    setDemoResult(null)

    try {
      const res = await fetch('/api/demo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(demoData),
      })

      const data = await res.json()

      if (!res.ok) {
        throw new Error(data.error || 'Demo failed')
      }

      setDemoResult(data.result)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setDemoLoading(false)
    }
  }

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
          <div className="flex items-center gap-4">
            <span className="text-sm text-[var(--muted)]">For Content Marketers</span>
            <Link href="/jobs" className="btn btn-secondary text-sm">
              My Jobs
            </Link>
          </div>
        </div>
      </nav>

      <main className="container py-12">
        <div className="max-w-4xl mx-auto">
          {/* Hero */}
          <div className="text-center mb-12">
            <div className="inline-block mb-4 px-3 py-1 bg-[var(--primary)] bg-opacity-10 text-[var(--primary)] text-sm rounded-full">
              AI-Powered Content Research
            </div>
            <h1 className="text-4xl md:text-5xl font-bold mb-4">
              Content Research Done
              <br />
              <span className="text-[var(--primary)]">In Minutes, Not Days</span>
            </h1>
            <p className="text-lg text-[var(--muted)] max-w-2xl mx-auto">
              Competitor analysis, SEO content, and content strategy — powered by AI with real web data.
              Pay only if you&apos;re satisfied.
            </p>
          </div>

          {/* Free Demo Section */}
          <div className="card mb-12 border-2 border-[var(--primary)] border-opacity-30">
            <div className="flex items-center gap-2 mb-4">
              <span className="text-2xl">🎁</span>
              <h2 className="text-xl font-semibold">Try Free: Instant Competitor Snapshot</h2>
            </div>
            <p className="text-[var(--muted)] mb-6">
              Enter any competitor URL and get a quick analysis in 30 seconds. No credit card required.
            </p>

            {!demoResult ? (
              <form onSubmit={handleDemoSubmit} className="space-y-4">
                <div className="grid md:grid-cols-2 gap-4">
                  <div>
                    <label className="label">Competitor URL</label>
                    <input
                      type="url"
                      className="input"
                      placeholder="https://competitor.com"
                      value={demoData.url}
                      onChange={(e) => setDemoData({ ...demoData, url: e.target.value })}
                      required
                    />
                  </div>
                  <div>
                    <label className="label">Your Email</label>
                    <input
                      type="email"
                      className="input"
                      placeholder="you@company.com"
                      value={demoData.email}
                      onChange={(e) => setDemoData({ ...demoData, email: e.target.value })}
                      required
                    />
                  </div>
                </div>
                <button
                  type="submit"
                  disabled={demoLoading}
                  className="btn btn-primary"
                >
                  {demoLoading ? (
                    <span className="flex items-center gap-2">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                      </svg>
                      Analyzing...
                    </span>
                  ) : (
                    <>Get Free Snapshot →</>
                  )}
                </button>
              </form>
            ) : (
              <div>
                <div className="bg-[var(--background)] rounded-lg p-6 mb-4 prose prose-invert max-w-none overflow-auto max-h-[400px]">
                  <pre className="whitespace-pre-wrap text-sm">{demoResult}</pre>
                </div>
                <div className="flex gap-4">
                  <button
                    onClick={() => {
                      setDemoResult(null)
                      setActiveTab('full')
                      setFormData({ ...formData, job_type: 'RESEARCHER', title: `Full competitor analysis: ${demoData.url}` })
                    }}
                    className="btn btn-primary"
                  >
                    Get Full Analysis ($15) →
                  </button>
                  <button
                    onClick={() => setDemoResult(null)}
                    className="btn btn-secondary"
                  >
                    Try Another
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Tabs */}
          <div className="flex gap-2 mb-6">
            <button
              onClick={() => setActiveTab('demo')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === 'demo'
                  ? 'bg-[var(--primary)] text-white'
                  : 'bg-[var(--card)] text-[var(--muted)] hover:text-[var(--foreground)]'
              }`}
            >
              Example Work
            </button>
            <button
              onClick={() => setActiveTab('full')}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                activeTab === 'full'
                  ? 'bg-[var(--primary)] text-white'
                  : 'bg-[var(--card)] text-[var(--muted)] hover:text-[var(--foreground)]'
              }`}
            >
              Create Job
            </button>
          </div>

          {activeTab === 'demo' && (
            <>
              {/* Agent Types */}
              <div className="grid md:grid-cols-3 gap-4 mb-8">
                {AGENT_TYPES.map((agent) => (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={() => {
                      setFormData({ ...formData, job_type: agent.id })
                      setActiveTab('full')
                    }}
                    className="card text-left transition-all hover:border-[var(--primary)] group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-3xl">{agent.icon}</span>
                      <span className="text-xs text-[var(--primary)] font-medium">{agent.price}</span>
                    </div>
                    <h3 className="font-semibold mb-1">{agent.name}</h3>
                    <p className="text-sm text-[var(--muted)] mb-3">{agent.description}</p>
                    <div className="flex flex-wrap gap-1">
                      {agent.examples.map((ex) => (
                        <span key={ex} className="badge badge-gray text-xs">
                          {ex}
                        </span>
                      ))}
                    </div>
                    <div className="mt-4 text-sm text-[var(--primary)] opacity-0 group-hover:opacity-100 transition-opacity">
                      Order now →
                    </div>
                  </button>
                ))}
              </div>

              {/* Example Work */}
              <div className="mb-12">
                <h2 className="text-xl font-semibold mb-6">Recent Work Examples</h2>
                <div className="space-y-4">
                  {EXAMPLE_WORK.map((work, i) => (
                    <div key={i} className="card">
                      <div className="flex items-start justify-between mb-2">
                        <div>
                          <span className="badge badge-gray text-xs mb-2">{work.type}</span>
                          <h3 className="font-semibold">{work.title}</h3>
                        </div>
                        <span className="text-xs text-[var(--muted)]">⚡ {work.time}</span>
                      </div>
                      <p className="text-sm text-[var(--muted)] mb-3">{work.preview}</p>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-[var(--primary)]">📦 {work.deliverable}</span>
                        <button
                          onClick={() => setActiveTab('full')}
                          className="text-sm text-[var(--primary)] hover:underline"
                        >
                          Order similar →
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {activeTab === 'full' && (
            <form onSubmit={handleSubmit} className="card">
              <h2 className="text-xl font-semibold mb-6">Create Your Job</h2>

              {error && (
                <div className="mb-6 p-4 bg-[var(--error)] bg-opacity-10 border border-[var(--error)] rounded-lg text-[var(--error)]">
                  {error}
                </div>
              )}

              {/* Agent Selection */}
              <div className="mb-6">
                <label className="label">What do you need?</label>
                <div className="grid md:grid-cols-3 gap-3">
                  {AGENT_TYPES.map((agent) => (
                    <button
                      key={agent.id}
                      type="button"
                      onClick={() => setFormData({ ...formData, job_type: agent.id })}
                      className={`p-3 rounded-lg border text-left transition-all ${
                        formData.job_type === agent.id
                          ? 'border-[var(--primary)] bg-[var(--primary)] bg-opacity-10'
                          : 'border-[var(--border)] hover:border-[var(--primary)]'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xl">{agent.icon}</span>
                        <span className="font-medium text-sm">{agent.name}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="label">Job Title</label>
                  <input
                    type="text"
                    className="input"
                    placeholder="e.g., Competitive analysis of Notion vs Coda"
                    value={formData.title}
                    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                    required
                  />
                </div>

                <div>
                  <label className="label">Description</label>
                  <textarea
                    className="input min-h-[150px]"
                    placeholder={`Be specific about what you need:\n- Target URLs or companies\n- Specific questions to answer\n- Desired format (report, blog post, calendar)\n- Any brand voice or style guidelines`}
                    value={formData.description}
                    onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                    required
                  />
                  <p className="text-xs text-[var(--muted)] mt-1">
                    Tip: Include competitor URLs for better analysis
                  </p>
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
                    <p className="text-xs text-[var(--muted)] mt-1">Min $5. Pay only if satisfied.</p>
                  </div>

                  <div>
                    <label className="label">Deadline (Optional)</label>
                    <input
                      type="text"
                      className="input"
                      placeholder="e.g., ASAP, By Friday"
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
                    placeholder="you@company.com"
                    value={formData.email}
                    onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                    required
                  />
                </div>

                <div className="pt-4">
                  <button
                    type="submit"
                    disabled={loading || !formData.job_type}
                    className="btn btn-primary w-full text-base py-4"
                  >
                    {loading ? (
                      <span className="flex items-center gap-2 justify-center">
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
          )}

          {/* Trust Signals */}
          <div className="mt-12 grid md:grid-cols-3 gap-6 text-center">
            <div>
              <div className="text-2xl mb-2">🔒</div>
              <h4 className="font-semibold mb-1">Escrow Protection</h4>
              <p className="text-sm text-[var(--muted)]">
                Payment held until you approve. Full refund if not satisfied.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">🌐</div>
              <h4 className="font-semibold mb-1">Real Web Data</h4>
              <p className="text-sm text-[var(--muted)]">
                AI researches live websites. No hallucinated data.
              </p>
            </div>
            <div>
              <div className="text-2xl mb-2">✅</div>
              <h4 className="font-semibold mb-1">Quality Verified</h4>
              <p className="text-sm text-[var(--muted)]">
                Every delivery checked by QA AI before you see it.
              </p>
            </div>
          </div>
        </div>
      </main>

      <footer className="border-t border-[var(--border)] py-8 mt-auto">
        <div className="container text-center text-sm text-[var(--muted)]">
          <p>AXAR Network — AI Content Research for Marketers</p>
          <p className="mt-1">Powered by Anthropic Claude • Secured by Stripe</p>
        </div>
      </footer>
    </div>
  )
}
