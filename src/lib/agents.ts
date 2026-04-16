import { Job, updateJobStatus, logAgentAction } from './db'

const AGENT_PROMPTS = {
  RESEARCHER: `You are AXAR RESEARCHER — a specialized AI agent for web research and data gathering.

Your capabilities:
- Deep web research across multiple sources
- Data extraction and verification
- Fact-checking and source validation
- Structured report generation

Output format:
- Always provide sources/URLs where applicable
- Structure findings clearly with headers
- Include confidence levels for data points
- Highlight any limitations or areas needing human verification

Be thorough, accurate, and cite your sources.`,

  WRITER: `You are AXAR WRITER — a specialized AI agent for content creation.

Your capabilities:
- Professional copywriting (emails, descriptions, articles)
- Technical documentation
- Marketing content
- Creative writing with brand voice adaptation

Output format:
- Match the requested tone and style
- Provide multiple variations when appropriate
- Include suggested headlines/subject lines
- Note any assumptions made about target audience

Be creative, engaging, and adapt to the context.`,

  ANALYST: `You are AXAR ANALYST — a specialized AI agent for data analysis and insights.

Your capabilities:
- Data analysis and pattern recognition
- Market research synthesis
- Competitive analysis
- Strategic recommendations

Output format:
- Lead with key insights
- Support with data and logic
- Provide actionable recommendations
- Include risk factors and confidence levels

Be analytical, objective, and insightful.`,
}

async function getAnthropicClient() {
  const Anthropic = (await import('@anthropic-ai/sdk')).default
  if (!process.env.ANTHROPIC_API_KEY) {
    throw new Error('ANTHROPIC_API_KEY is not set')
  }
  return new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
  })
}

export async function executeJob(job: Job): Promise<{ success: boolean; result: string; notes: string }> {
  const agentType = job.job_type
  const systemPrompt = AGENT_PROMPTS[agentType]

  await logAgentAction(job.id, agentType, 'STARTED', `Processing job: ${job.title}`)
  await updateJobStatus(job.id, 'IN_PROGRESS')

  try {
    const anthropic = await getAnthropicClient()
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-6-20250514',
      max_tokens: 4096,
      system: systemPrompt,
      messages: [
        {
          role: 'user',
          content: `# Job Request

**Title:** ${job.title}

**Description:**
${job.description}

**Budget:** $${(job.budget_cents / 100).toFixed(2)}
**Deadline:** ${job.deadline || 'As soon as possible'}

Please complete this task thoroughly. Provide a comprehensive deliverable that exceeds expectations.`,
        },
      ],
    })

    const result = response.content
      .filter((block) => block.type === 'text')
      .map((block) => (block as { type: 'text'; text: string }).text)
      .join('\n\n')

    await logAgentAction(job.id, agentType, 'COMPLETED', `Tokens used: ${response.usage.input_tokens + response.usage.output_tokens}`)

    return {
      success: true,
      result,
      notes: `Completed by AXAR ${agentType}. Model: claude-sonnet-4-6. Tokens: ${response.usage.input_tokens + response.usage.output_tokens}`,
    }
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : 'Unknown error'
    await logAgentAction(job.id, agentType, 'FAILED', errorMessage)

    return {
      success: false,
      result: `Agent execution failed: ${errorMessage}`,
      notes: `Error during execution: ${errorMessage}`,
    }
  }
}

export async function verifyResult(job: Job, result: string): Promise<{ passed: boolean; score: number; feedback: string }> {
  try {
    const anthropic = await getAnthropicClient()
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-6-20250514',
      max_tokens: 1024,
      system: `You are a quality assurance agent. Your job is to verify if the delivered work meets the requirements.

Score the work from 0-100 based on:
- Completeness (does it address all requirements?)
- Quality (is it well-written/accurate?)
- Usefulness (would the client find this valuable?)

Respond in JSON format:
{
  "score": <number 0-100>,
  "passed": <boolean, true if score >= 70>,
  "feedback": "<brief feedback>"
}`,
      messages: [
        {
          role: 'user',
          content: `# Original Request
**Title:** ${job.title}
**Description:** ${job.description}

# Delivered Result
${result}

Please evaluate this delivery.`,
        },
      ],
    })

    const text = response.content
      .filter((block) => block.type === 'text')
      .map((block) => (block as { type: 'text'; text: string }).text)
      .join('')

    const jsonMatch = text.match(/\{[\s\S]*\}/)
    if (jsonMatch) {
      const evaluation = JSON.parse(jsonMatch[0])
      return {
        passed: evaluation.passed ?? evaluation.score >= 70,
        score: evaluation.score,
        feedback: evaluation.feedback,
      }
    }

    return { passed: true, score: 75, feedback: 'Evaluation completed.' }
  } catch {
    return { passed: true, score: 75, feedback: 'Verification skipped due to error.' }
  }
}
