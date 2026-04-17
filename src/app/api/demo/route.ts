import { NextRequest, NextResponse } from 'next/server'
import { runFreeDemo } from '@/lib/agents'
import { z } from 'zod'

const demoSchema = z.object({
  url: z.string().url('Please enter a valid URL'),
  email: z.string().email('Please enter a valid email'),
})

// Rate limiting - simple in-memory store (use Redis in production)
const rateLimitStore = new Map<string, { count: number; resetAt: number }>()

function checkRateLimit(email: string): boolean {
  const now = Date.now()
  const limit = rateLimitStore.get(email)

  if (!limit || limit.resetAt < now) {
    rateLimitStore.set(email, { count: 1, resetAt: now + 24 * 60 * 60 * 1000 }) // 24h window
    return true
  }

  if (limit.count >= 3) { // Max 3 demos per email per day
    return false
  }

  limit.count++
  return true
}

export async function POST(request: NextRequest) {
  try {
    const body = await request.json()
    const validation = demoSchema.safeParse(body)

    if (!validation.success) {
      return NextResponse.json(
        { error: validation.error.issues[0].message },
        { status: 400 }
      )
    }

    const { url, email } = validation.data

    // Check rate limit
    if (!checkRateLimit(email)) {
      return NextResponse.json(
        { error: 'Demo limit reached. Upgrade to get unlimited analyses.' },
        { status: 429 }
      )
    }

    const result = await runFreeDemo(url, email)

    if (!result.success) {
      return NextResponse.json(
        { error: result.result },
        { status: 400 }
      )
    }

    return NextResponse.json({
      success: true,
      result: result.result,
      cta: {
        message: 'Want the full 10-page analysis with actionable recommendations?',
        upgradeUrl: '/?prefill=competitor-analysis',
      },
    })
  } catch (error) {
    console.error('Demo error:', error)
    return NextResponse.json(
      { error: 'Failed to generate demo. Please try again.' },
      { status: 500 }
    )
  }
}
