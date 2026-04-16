import { NextRequest, NextResponse } from 'next/server'
import Stripe from 'stripe'
import db, { getJob, updateJobStatus, logAgentAction } from '@/lib/db'
import { executeJob, verifyResult } from '@/lib/agents'
import { getPaymentIntent } from '@/lib/stripe'
import { sendPaymentConfirmation, sendResultReady, sendJobNotification } from '@/lib/email'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  apiVersion: '2026-03-25.dahlia',
})

const webhookSecret = process.env.STRIPE_WEBHOOK_SECRET!

export async function POST(request: NextRequest) {
  const body = await request.text()
  const signature = request.headers.get('stripe-signature')!

  let event: Stripe.Event

  try {
    event = stripe.webhooks.constructEvent(body, signature, webhookSecret)
  } catch (err) {
    console.error('Webhook signature verification failed:', err)
    return NextResponse.json({ error: 'Invalid signature' }, { status: 400 })
  }

  if (event.type === 'checkout.session.completed') {
    const session = event.data.object as Stripe.Checkout.Session
    const jobId = session.metadata?.job_id

    if (!jobId) {
      console.error('No job_id in session metadata')
      return NextResponse.json({ error: 'No job_id' }, { status: 400 })
    }

    const job = getJob(jobId)
    if (!job) {
      console.error('Job not found:', jobId)
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    const paymentIntentId = await getPaymentIntent(session.id)

    const stmt = db.prepare(`
      UPDATE jobs SET stripe_session_id = ?, stripe_payment_id = ?
      WHERE id = ?
    `)
    stmt.run(session.id, paymentIntentId, jobId)

    try {
      await sendPaymentConfirmation(job.email, jobId, job.title, job.budget_cents)
    } catch (e) {
      console.error('Failed to send payment confirmation email:', e)
    }

    console.log(`Job ${jobId}: Payment authorized, starting agent work`)
    logAgentAction(jobId, job.job_type, 'PAYMENT_AUTHORIZED', `Session: ${session.id}`)

    try {
      const result = await executeJob(job)

      if (result.success) {
        const verification = await verifyResult(job, result.result)

        if (verification.passed) {
          updateJobStatus(jobId, 'COMPLETED', result.result, result.notes)
          logAgentAction(jobId, job.job_type, 'QA_PASSED', `Score: ${verification.score}`)

          try {
            const preview = result.result.slice(0, 300)
            await sendResultReady(job.email, jobId, job.title, preview)
          } catch (e) {
            console.error('Failed to send result email:', e)
          }
        } else {
          updateJobStatus(jobId, 'FAILED', result.result, `QA Failed: ${verification.feedback}`)
          logAgentAction(jobId, job.job_type, 'QA_FAILED', verification.feedback)

          try {
            await sendJobNotification(job.email, jobId, job.title, 'failed')
          } catch (e) {
            console.error('Failed to send failure email:', e)
          }
        }
      } else {
        updateJobStatus(jobId, 'FAILED', result.result, result.notes)

        try {
          await sendJobNotification(job.email, jobId, job.title, 'failed')
        } catch (e) {
          console.error('Failed to send failure email:', e)
        }
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error'
      updateJobStatus(jobId, 'FAILED', `Agent error: ${errorMessage}`, errorMessage)
      logAgentAction(jobId, job.job_type, 'ERROR', errorMessage)

      try {
        await sendJobNotification(job.email, jobId, job.title, 'failed')
      } catch (e) {
        console.error('Failed to send failure email:', e)
      }
    }
  }

  return NextResponse.json({ received: true })
}
