import { Resend } from 'resend'

const resend = new Resend(process.env.RESEND_API_KEY)

export async function sendJobNotification(
  email: string,
  jobId: string,
  title: string,
  status: 'submitted' | 'completed' | 'failed'
): Promise<void> {
  const subjects = {
    submitted: `Job Submitted: ${title}`,
    completed: `Job Complete: ${title}`,
    failed: `Job Failed: ${title}`,
  }

  const messages = {
    submitted: `Your job "${title}" has been submitted and is pending payment. Once payment is confirmed, an AXAR agent will begin work immediately.`,
    completed: `Great news! Your job "${title}" has been completed by an AXAR agent. You can review the results and approve payment at the link below.`,
    failed: `Unfortunately, your job "${title}" could not be completed. Your payment has been automatically refunded.`,
  }

  const jobUrl = `${process.env.NEXT_PUBLIC_APP_URL}/jobs/${jobId}`

  await resend.emails.send({
    from: 'AXAR Network <noreply@axar.network>',
    to: email,
    subject: subjects[status],
    html: `
      <!DOCTYPE html>
      <html>
        <head>
          <meta charset="utf-8">
          <style>
            body { font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; color: #1c1c1c; }
            .container { max-width: 600px; margin: 0 auto; padding: 40px 20px; }
            .logo { font-size: 24px; font-weight: bold; color: #0066ff; margin-bottom: 30px; }
            .message { font-size: 16px; margin-bottom: 30px; }
            .button {
              display: inline-block;
              background: #0066ff;
              color: white;
              padding: 12px 24px;
              border-radius: 6px;
              text-decoration: none;
              font-weight: 500;
            }
            .footer { margin-top: 40px; font-size: 14px; color: #666; }
          </style>
        </head>
        <body>
          <div class="container">
            <div class="logo">AXAR Network</div>
            <p class="message">${messages[status]}</p>
            <a href="${jobUrl}" class="button">View Job Details</a>
            <p class="footer">
              AXAR Network — AI agents that get paid only when you're satisfied.<br>
              <small>Powered by Anthropic Claude • Secured by Stripe</small>
            </p>
          </div>
        </body>
      </html>
    `,
  })
}

export async function sendPaymentConfirmation(
  email: string,
  jobId: string,
  title: string,
  amountCents: number
): Promise<void> {
  const amount = (amountCents / 100).toFixed(2)
  const jobUrl = `${process.env.NEXT_PUBLIC_APP_URL}/jobs/${jobId}`

  await resend.emails.send({
    from: 'AXAR Network <noreply@axar.network>',
    to: email,
    subject: `Payment Authorized: $${amount}`,
    html: `
      <!DOCTYPE html>
      <html>
        <head>
          <meta charset="utf-8">
          <style>
            body { font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; color: #1c1c1c; }
            .container { max-width: 600px; margin: 0 auto; padding: 40px 20px; }
            .logo { font-size: 24px; font-weight: bold; color: #0066ff; margin-bottom: 30px; }
            .amount { font-size: 32px; font-weight: bold; color: #0066ff; margin: 20px 0; }
            .message { font-size: 16px; margin-bottom: 30px; }
            .button {
              display: inline-block;
              background: #0066ff;
              color: white;
              padding: 12px 24px;
              border-radius: 6px;
              text-decoration: none;
              font-weight: 500;
            }
            .note { background: #f5f5f5; padding: 15px; border-radius: 6px; font-size: 14px; margin: 20px 0; }
            .footer { margin-top: 40px; font-size: 14px; color: #666; }
          </style>
        </head>
        <body>
          <div class="container">
            <div class="logo">AXAR Network</div>
            <p class="message">Your payment has been authorized for job "${title}"</p>
            <div class="amount">$${amount}</div>
            <div class="note">
              <strong>How escrow works:</strong><br>
              Your payment is held securely by Stripe. The agent is now working on your job.
              You will only be charged when you approve the completed work.
              If you're not satisfied, you can request a refund.
            </div>
            <a href="${jobUrl}" class="button">Track Progress</a>
            <p class="footer">
              AXAR Network — Pay only when you're satisfied.<br>
              <small>Questions? Reply to this email.</small>
            </p>
          </div>
        </body>
      </html>
    `,
  })
}

export async function sendResultReady(
  email: string,
  jobId: string,
  title: string,
  preview: string
): Promise<void> {
  const jobUrl = `${process.env.NEXT_PUBLIC_APP_URL}/jobs/${jobId}`

  await resend.emails.send({
    from: 'AXAR Network <noreply@axar.network>',
    to: email,
    subject: `✅ Results Ready: ${title}`,
    html: `
      <!DOCTYPE html>
      <html>
        <head>
          <meta charset="utf-8">
          <style>
            body { font-family: -apple-system, system-ui, sans-serif; line-height: 1.6; color: #1c1c1c; }
            .container { max-width: 600px; margin: 0 auto; padding: 40px 20px; }
            .logo { font-size: 24px; font-weight: bold; color: #0066ff; margin-bottom: 30px; }
            .message { font-size: 16px; margin-bottom: 20px; }
            .preview { background: #f5f5f5; padding: 20px; border-radius: 8px; font-size: 14px; margin: 20px 0; white-space: pre-wrap; }
            .buttons { margin: 30px 0; }
            .button {
              display: inline-block;
              background: #0066ff;
              color: white;
              padding: 12px 24px;
              border-radius: 6px;
              text-decoration: none;
              font-weight: 500;
              margin-right: 10px;
            }
            .button-secondary { background: #28a745; }
            .footer { margin-top: 40px; font-size: 14px; color: #666; }
          </style>
        </head>
        <body>
          <div class="container">
            <div class="logo">AXAR Network</div>
            <p class="message">Your AI agent has completed the job: <strong>${title}</strong></p>
            <p class="message">Here's a preview:</p>
            <div class="preview">${preview.slice(0, 500)}${preview.length > 500 ? '...' : ''}</div>
            <div class="buttons">
              <a href="${jobUrl}" class="button">Review Full Results</a>
              <a href="${jobUrl}?action=approve" class="button button-secondary">Approve & Pay</a>
            </div>
            <p class="footer">
              Not satisfied? You can request revisions or a full refund.<br>
              <small>Your payment is held in escrow until you approve.</small>
            </p>
          </div>
        </body>
      </html>
    `,
  })
}
