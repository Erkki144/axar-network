import { createClient, Client } from '@libsql/client'

let db: Client | null = null

function getDb(): Client {
  if (db) return db

  const url = process.env.DATABASE_URL || 'file:./data/axar.db'
  const authToken = process.env.DATABASE_AUTH_TOKEN

  db = createClient({
    url,
    authToken,
  })

  // Initialize tables
  db.executeMultiple(`
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      job_type TEXT NOT NULL CHECK(job_type IN ('RESEARCHER', 'WRITER', 'ANALYST')),
      budget_cents INTEGER NOT NULL,
      deadline TEXT,
      email TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'OPEN' CHECK(status IN ('OPEN', 'IN_PROGRESS', 'COMPLETED', 'FAILED', 'REFUNDED')),
      stripe_payment_id TEXT,
      stripe_session_id TEXT,
      result TEXT,
      agent_notes TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      started_at TEXT,
      completed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS agent_logs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id TEXT NOT NULL,
      agent_type TEXT NOT NULL,
      action TEXT NOT NULL,
      details TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (job_id) REFERENCES jobs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
  `)

  return db
}

export default {
  prepare: (sql: string) => ({
    run: async (...params: unknown[]) => {
      const client = getDb()
      await client.execute({ sql, args: params as (string | number | null)[] })
    },
    get: async (...params: unknown[]) => {
      const client = getDb()
      const result = await client.execute({ sql, args: params as (string | number | null)[] })
      return result.rows[0] || null
    },
    all: async (...params: unknown[]) => {
      const client = getDb()
      const result = await client.execute({ sql, args: params as (string | number | null)[] })
      return result.rows
    },
  }),
}

export interface Job {
  id: string
  title: string
  description: string
  job_type: 'RESEARCHER' | 'WRITER' | 'ANALYST'
  budget_cents: number
  deadline: string | null
  email: string
  status: 'OPEN' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'REFUNDED'
  stripe_payment_id: string | null
  stripe_session_id: string | null
  result: string | null
  agent_notes: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export async function createJob(job: Omit<Job, 'created_at' | 'started_at' | 'completed_at' | 'status' | 'result' | 'agent_notes'>): Promise<Job> {
  const client = getDb()
  await client.execute({
    sql: `INSERT INTO jobs (id, title, description, job_type, budget_cents, deadline, email, stripe_payment_id, stripe_session_id)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    args: [job.id, job.title, job.description, job.job_type, job.budget_cents, job.deadline, job.email, job.stripe_payment_id, job.stripe_session_id]
  })
  return (await getJob(job.id))!
}

export async function getJob(id: string): Promise<Job | null> {
  const client = getDb()
  const result = await client.execute({ sql: 'SELECT * FROM jobs WHERE id = ?', args: [id] })
  return result.rows[0] as unknown as Job | null
}

export async function getOpenJobs(): Promise<Job[]> {
  const client = getDb()
  const result = await client.execute({ sql: 'SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC', args: ['OPEN'] })
  return result.rows as unknown as Job[]
}

export async function getAllJobs(): Promise<Job[]> {
  const client = getDb()
  const result = await client.execute({ sql: 'SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100', args: [] })
  return result.rows as unknown as Job[]
}

export async function updateJobStatus(id: string, status: Job['status'], result?: string, agentNotes?: string): Promise<void> {
  const client = getDb()
  const updates: string[] = ['status = ?']
  const params: (string | null)[] = [status]

  if (status === 'IN_PROGRESS') {
    updates.push("started_at = datetime('now')")
  }
  if (status === 'COMPLETED' || status === 'FAILED') {
    updates.push("completed_at = datetime('now')")
  }
  if (result !== undefined) {
    updates.push('result = ?')
    params.push(result)
  }
  if (agentNotes !== undefined) {
    updates.push('agent_notes = ?')
    params.push(agentNotes)
  }

  params.push(id)
  await client.execute({ sql: `UPDATE jobs SET ${updates.join(', ')} WHERE id = ?`, args: params })
}

export async function logAgentAction(jobId: string, agentType: string, action: string, details?: string): Promise<void> {
  const client = getDb()
  await client.execute({
    sql: 'INSERT INTO agent_logs (job_id, agent_type, action, details) VALUES (?, ?, ?, ?)',
    args: [jobId, agentType, action, details || null]
  })
}
