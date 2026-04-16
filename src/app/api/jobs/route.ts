import { NextRequest, NextResponse } from 'next/server'
import { createJob, getAllJobs, Job } from '@/lib/db'
import { createCheckoutSession } from '@/lib/stripe'
import { nanoid } from 'nanoid'

export async function GET() {
  try {
    const jobs = await getAllJobs()
    return NextResponse.json(jobs)
  } catch (error) {
    console.error('Failed to get jobs:', error)
    return NextResponse.json({ error: 'Failed to fetch jobs' }, { status: 500 })
  }
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const { title, description, job_type, budget, deadline, email } = body

    if (!title || !description || !job_type || !budget || !email) {
      return NextResponse.json(
        { error: 'Missing required fields: title, description, job_type, budget, email' },
        { status: 400 }
      )
    }

    const validTypes = ['RESEARCHER', 'WRITER', 'ANALYST']
    if (!validTypes.includes(job_type)) {
      return NextResponse.json(
        { error: `Invalid job_type. Must be one of: ${validTypes.join(', ')}` },
        { status: 400 }
      )
    }

    const budgetCents = Math.round(parseFloat(budget) * 100)
    if (budgetCents < 500) {
      return NextResponse.json(
        { error: 'Minimum budget is $5.00' },
        { status: 400 }
      )
    }

    const jobId = nanoid(12)

    const job = await createJob({
      id: jobId,
      title,
      description,
      job_type: job_type as Job['job_type'],
      budget_cents: budgetCents,
      deadline: deadline || null,
      email,
      stripe_payment_id: null,
      stripe_session_id: null,
    })

    const session = await createCheckoutSession(jobId, title, budgetCents, email)

    return NextResponse.json({
      job,
      checkoutUrl: session.url,
    })
  } catch (error) {
    console.error('Failed to create job:', error)
    const message = error instanceof Error ? error.message : 'Unknown error'
    return NextResponse.json(
      { error: 'Failed to create job', details: message },
      { status: 500 }
    )
  }
}
