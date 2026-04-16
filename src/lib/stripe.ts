import Stripe from 'stripe'

export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  apiVersion: '2026-03-25.dahlia',
})

export async function createCheckoutSession(
  jobId: string,
  title: string,
  budgetCents: number,
  email: string
): Promise<Stripe.Checkout.Session> {
  const session = await stripe.checkout.sessions.create({
    payment_method_types: ['card'],
    line_items: [
      {
        price_data: {
          currency: 'usd',
          product_data: {
            name: `AXAR Job: ${title}`,
            description: 'AI agent work - Pay only if satisfied',
          },
          unit_amount: budgetCents,
        },
        quantity: 1,
      },
    ],
    mode: 'payment',
    success_url: `${process.env.NEXT_PUBLIC_APP_URL}/jobs/${jobId}?success=true`,
    cancel_url: `${process.env.NEXT_PUBLIC_APP_URL}/jobs/${jobId}?canceled=true`,
    customer_email: email,
    metadata: {
      job_id: jobId,
    },
    payment_intent_data: {
      capture_method: 'manual', // Escrow: capture only after completion
      metadata: {
        job_id: jobId,
      },
    },
  })

  return session
}

export async function capturePayment(paymentIntentId: string): Promise<void> {
  await stripe.paymentIntents.capture(paymentIntentId)
}

export async function cancelPayment(paymentIntentId: string): Promise<void> {
  await stripe.paymentIntents.cancel(paymentIntentId)
}

export async function getPaymentIntent(sessionId: string): Promise<string | null> {
  const session = await stripe.checkout.sessions.retrieve(sessionId)
  return session.payment_intent as string | null
}
