import Database from 'better-sqlite3'
import path from 'path'
import fs from 'fs'

let db: ReturnType<typeof Database> | null = null

function getDb(): ReturnType<typeof Database> {
  if (db) return db

  const dbPath = process.env.DATABASE_PATH || path.join(process.cwd(), 'data', 'axar.db')

  const dataDir = path.dirname(dbPath)
  if (!fs.existsSync(dataDir)) {
    fs.mkdirSync(dataDir, { recursive: true })
  }

  db = new Database(dbPath)

  db.exec(`
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
  prepare: (sql: string) => getDb().prepare(sql),
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

export function createJob(job: Omit<Job, 'created_at' | 'started_at' | 'completed_at' | 'status' | 'result' | 'agent_notes'>): Job {
  const stmt = getDb().prepare(`
    INSERT INTO jobs (id, title, description, job_type, budget_cents, deadline, email, stripe_payment_id, stripe_session_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `)
  stmt.run(job.id, job.title, job.description, job.job_type, job.budget_cents, job.deadline, job.email, job.stripe_payment_id, job.stripe_session_id)
  return getJob(job.id)!
}

export function getJob(id: string): Job | null {
  const stmt = getDb().prepare('SELECT * FROM jobs WHERE id = ?')
  return stmt.get(id) as Job | null
}

export function getOpenJobs(): Job[] {
  const stmt = getDb().prepare('SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC')
  return stmt.all('OPEN') as Job[]
}

export function getAllJobs(): Job[] {
  const stmt = getDb().prepare('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100')
  return stmt.all() as Job[]
}

export function updateJobStatus(id: string, status: Job['status'], result?: string, agentNotes?: string): void {
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
  const stmt = getDb().prepare(`UPDATE jobs SET ${updates.join(', ')} WHERE id = ?`)
  stmt.run(...params)
}

export function logAgentAction(jobId: string, agentType: string, action: string, details?: string): void {
  const stmt = getDb().prepare('INSERT INTO agent_logs (job_id, agent_type, action, details) VALUES (?, ?, ?, ?)')
  stmt.run(jobId, agentType, action, details || null)
}
