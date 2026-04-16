import { NextRequest, NextResponse } from 'next/server'
import { getJob, updateJobStatus } from '@/lib/db'
import { capturePayment, cancelPayment } from '@/lib/stripe'

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params

  try {
    const job = getJob(id)
    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }
    return NextResponse.json(job)
  } catch (error) {
    console.error('Failed to get job:', error)
    return NextResponse.json({ error: 'Failed to fetch job' }, { status: 500 })
  }
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const url = new URL(request.url)
  const action = url.searchParams.get('action')

  try {
    const job = getJob(id)
    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 })
    }

    if (action === 'approve') {
      if (job.status !== 'COMPLETED') {
        return NextResponse.json(
          { error: 'Job must be completed before approval' },
          { status: 400 }
        )
      }

      if (!job.stripe_payment_id) {
        return NextResponse.json(
          { error: 'No payment to capture' },
          { status: 400 }
        )
      }

      await capturePayment(job.stripe_payment_id)

      return NextResponse.json({
        message: 'Payment captured successfully',
        job,
      })
    }

    if (action === 'refund') {
      if (!job.stripe_payment_id) {
        return NextResponse.json(
          { error: 'No payment to refund' },
          { status: 400 }
        )
      }

      await cancelPayment(job.stripe_payment_id)
      updateJobStatus(id, 'REFUNDED')

      return NextResponse.json({
        message: 'Payment refunded successfully',
        job: getJob(id),
      })
    }

    return NextResponse.json(
      { error: 'Invalid action. Use ?action=approve or ?action=refund' },
      { status: 400 }
    )
  } catch (error) {
    console.error('Failed to process action:', error)
    return NextResponse.json(
      { error: 'Failed to process action' },
      { status: 500 }
    )
  }
}
