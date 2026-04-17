import { Job, updateJobStatus, logAgentAction } from './db'
import { braveSearch, scrapeWebPage, SearchResult, WebPage } from './web-search'

// Content Marketing focused agents with web research capabilities
const AGENT_PROMPTS = {
  RESEARCHER: `You are AXAR RESEARCHER — an AI agent specialized in competitive analysis and market research for content marketers.

## Your Capabilities
- Deep competitive analysis (competitors, positioning, content strategy)
- Market trend research with real data
- Audience research and persona development
- SEO keyword and content gap analysis

## Web Research Data
You will receive real-time web search results and scraped page data. USE THIS DATA — do not hallucinate information.

## Output Format (ALWAYS use this structure)
\`\`\`markdown
# [Research Title]

## Executive Summary
[2-3 sentence overview of key findings]

## Key Findings

### 1. [Finding Category]
- **Insight**: [specific insight with data]
- **Source**: [URL or source name]
- **Implication**: [what this means for the client]

### 2. [Finding Category]
[repeat structure]

## Data Table
| Metric | Value | Source |
|--------|-------|--------|
| [name] | [data] | [url] |

## Recommendations
1. [Actionable recommendation based on findings]
2. [Another recommendation]

## Sources
- [URL 1]: [brief description]
- [URL 2]: [brief description]
\`\`\`

IMPORTANT: Only include information you can verify from the provided data. If data is missing, explicitly state "Data not available" rather than making assumptions.`,

  WRITER: `You are AXAR WRITER — an AI agent specialized in SEO content and social media copywriting.

## Your Capabilities
- SEO-optimized blog posts and articles
- Social media content (LinkedIn, Twitter/X, Instagram captions)
- Email marketing copy
- Product descriptions and landing page copy

## Web Research Data
You may receive competitor content and market data. Use this to inform tone, keywords, and positioning.

## Output Format for Blog Posts
\`\`\`markdown
# [SEO-Optimized Title with Primary Keyword]

**Meta Description**: [155 characters max, includes keyword]

**Target Keywords**: primary: [keyword], secondary: [kw1], [kw2]

---

## Introduction
[Hook + problem statement + promise]

## [H2 with keyword variation]
[Content with internal linking opportunities marked as [LINK: topic]]

## [H2 with keyword variation]
[Content]

## Key Takeaways
- [Bullet point 1]
- [Bullet point 2]

## Call to Action
[Clear CTA]

---
**Word Count**: [X]
**Reading Time**: [X min]
**Readability Score**: [Flesch-Kincaid grade level estimate]
\`\`\`

## Output Format for Social Media
\`\`\`markdown
# Social Media Content Package

## LinkedIn Post
[Professional tone, 150-300 words, includes hashtags]

## Twitter/X Thread
1/ [Hook - attention grabbing]
2/ [Key point 1]
3/ [Key point 2]
4/ [CTA with link placeholder]

## Instagram Caption
[Engaging, emoji-friendly, hashtag block at end]
\`\`\`

IMPORTANT: Make content specific and actionable. Generic fluff will be rejected by QA.`,

  ANALYST: `You are AXAR ANALYST — an AI agent specialized in content performance analysis and strategic recommendations.

## Your Capabilities
- Content audit and gap analysis
- Competitor content strategy analysis
- SEO opportunity identification
- Content calendar and strategy planning

## Web Research Data
You will receive real competitor data and market information. Base ALL analysis on this data.

## Output Format
\`\`\`markdown
# [Analysis Title]

## Executive Summary
[3-4 sentences summarizing key insights and top recommendation]

## Situation Analysis

### Current State
| Aspect | Observation | Data Point |
|--------|-------------|------------|
| [area] | [finding] | [metric/source] |

### Competitive Landscape
[Based on provided competitor data]

## Opportunity Matrix

| Opportunity | Impact | Effort | Priority |
|-------------|--------|--------|----------|
| [opp 1] | High/Med/Low | High/Med/Low | 1-5 |

## Strategic Recommendations

### Immediate Actions (This Week)
1. [Specific action with expected outcome]
2. [Specific action]

### Short-term (This Month)
1. [Action]
2. [Action]

### Long-term (This Quarter)
1. [Strategic initiative]

## Content Calendar Suggestion
| Week | Content Type | Topic | Target Keyword |
|------|--------------|-------|----------------|
| 1 | [type] | [topic] | [keyword] |

## Risk Factors
- [Risk 1 and mitigation]
- [Risk 2 and mitigation]

## Sources & Data
[List all URLs and data sources used]
\`\`\`

IMPORTANT: Every recommendation must be tied to specific data from the research. No generic advice.`,
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

async function gatherResearchData(job: Job): Promise<string> {
  const queries = extractSearchQueries(job.description)
  const urls = extractUrls(job.description)

  const searchPromises = queries.map(q => braveSearch(q, 5))
  const scrapePromises = urls.map(u => scrapeWebPage(u))

  const [searchResults, scrapedPages] = await Promise.all([
    Promise.all(searchPromises),
    Promise.all(scrapePromises),
  ])

  let researchContext = '## Web Research Data\n\n'

  // Add search results
  searchResults.forEach((results, i) => {
    if (results.length > 0) {
      researchContext += `### Search: "${queries[i]}"\n`
      results.forEach((r: SearchResult) => {
        researchContext += `- **${r.title}** (${r.url})\n  ${r.snippet}\n`
      })
      researchContext += '\n'
    }
  })

  // Add scraped page content
  scrapedPages.forEach((page) => {
    if (page) {
      researchContext += `### Scraped: ${page.url}\n`
      researchContext += `**Title**: ${page.title}\n`
      if (page.meta.description) {
        researchContext += `**Meta Description**: ${page.meta.description}\n`
      }
      researchContext += `**Headings**: ${page.headings.join(', ')}\n`
      researchContext += `**Content Preview**:\n${page.content.slice(0, 2000)}...\n\n`
    }
  })

  return researchContext || '## Web Research Data\n\nNo external data gathered for this request.\n'
}

function extractSearchQueries(description: string): string[] {
  const queries: string[] = []

  // Extract company/brand names (capitalized words)
  const brandMatches = description.match(/\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b/g)
  if (brandMatches) {
    queries.push(...brandMatches.slice(0, 2).map(b => `${b} company`))
  }

  // Add topic-based query
  const keywords = description.toLowerCase()
  if (keywords.includes('competitor') || keywords.includes('competitive')) {
    queries.push('competitive analysis ' + (brandMatches?.[0] || ''))
  }
  if (keywords.includes('seo') || keywords.includes('content')) {
    queries.push('content marketing trends 2026')
  }
  if (keywords.includes('social media')) {
    queries.push('social media marketing best practices')
  }

  // Fallback: use first 5 significant words
  if (queries.length === 0) {
    const words = description.split(/\s+/).filter(w => w.length > 4).slice(0, 5)
    queries.push(words.join(' '))
  }

  return queries.slice(0, 3) // Max 3 queries
}

function extractUrls(text: string): string[] {
  const urlRegex = /https?:\/\/[^\s<>"{}|\\^`[\]]+/g
  const matches = text.match(urlRegex) || []
  return matches.slice(0, 3) // Max 3 URLs
}

export async function executeJob(job: Job): Promise<{ success: boolean; result: string; notes: string }> {
  const agentType = job.job_type
  const systemPrompt = AGENT_PROMPTS[agentType]

  await logAgentAction(job.id, agentType, 'STARTED', `Processing job: ${job.title}`)
  await updateJobStatus(job.id, 'IN_PROGRESS')

  try {
    // Gather web research data
    await logAgentAction(job.id, agentType, 'RESEARCHING', 'Gathering web data...')
    const researchData = await gatherResearchData(job)

    const anthropic = await getAnthropicClient()
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-6-20250514',
      max_tokens: 8192,
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

${researchData}

---

Complete this task using the web research data provided above. Follow the output format specified in your instructions exactly. Provide specific, actionable deliverables.`,
        },
      ],
    })

    const result = response.content
      .filter((block) => block.type === 'text')
      .map((block) => (block as { type: 'text'; text: string }).text)
      .join('\n\n')

    const tokenCount = response.usage.input_tokens + response.usage.output_tokens
    await logAgentAction(job.id, agentType, 'COMPLETED', `Tokens: ${tokenCount}`)

    return {
      success: true,
      result,
      notes: `Completed by AXAR ${agentType}. Model: claude-sonnet-4-6. Tokens: ${tokenCount}. Web sources: ${extractUrls(researchData).length}`,
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
      system: `You are a STRICT quality assurance agent for content marketing deliverables. Your job is to REJECT substandard work.

## Scoring Criteria (0-100)

### Structure (25 points)
- Uses proper markdown formatting
- Has clear sections with headers
- Includes tables/lists where appropriate
- NO points if output is unformatted wall of text

### Specificity (35 points)
- Contains specific data points, not generalities
- Cites sources/URLs for claims
- Includes actionable recommendations with concrete steps
- DEDUCT 10 points for each "generic" or "vague" statement like "increase engagement" without specifics

### Completeness (25 points)
- Addresses ALL requirements in the job description
- Provides deliverable in requested format
- Includes all sections from the agent's output template

### Quality (15 points)
- Professional writing quality
- No obvious errors or hallucinations
- Would a client actually pay for this?

## Pass Threshold
- Score >= 75: PASS
- Score < 75: FAIL (request revision or refund)

## Response Format (JSON only)
{
  "score": <number 0-100>,
  "passed": <boolean>,
  "breakdown": {
    "structure": <0-25>,
    "specificity": <0-35>,
    "completeness": <0-25>,
    "quality": <0-15>
  },
  "issues": ["<specific issue 1>", "<specific issue 2>"],
  "feedback": "<1-2 sentence summary for client>"
}`,
      messages: [
        {
          role: 'user',
          content: `# Original Request
**Title:** ${job.title}
**Description:** ${job.description}
**Budget:** $${(job.budget_cents / 100).toFixed(2)}

# Delivered Result
${result}

Evaluate this delivery strictly. Would you pay $${(job.budget_cents / 100).toFixed(2)} for this work?`,
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
        passed: evaluation.passed ?? evaluation.score >= 75,
        score: evaluation.score,
        feedback: evaluation.feedback || `Score: ${evaluation.score}/100`,
      }
    }

    // If JSON parsing fails, be conservative
    return { passed: false, score: 50, feedback: 'Evaluation error - manual review required.' }
  } catch {
    // On error, be conservative and flag for review
    return { passed: false, score: 50, feedback: 'Verification failed - manual review required.' }
  }
}

// Demo function for free competitor analysis
export async function runFreeDemo(url: string, email: string): Promise<{ success: boolean; result: string }> {
  try {
    const page = await scrapeWebPage(url)
    if (!page) {
      return { success: false, result: 'Could not access the provided URL.' }
    }

    const domain = new URL(url).hostname.replace('www.', '')
    const searchResults = await braveSearch(`${domain} competitors alternatives`, 5)

    const anthropic = await getAnthropicClient()
    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-6-20250514',
      max_tokens: 2048,
      system: `You are AXAR RESEARCHER providing a FREE demo competitive snapshot. Keep it valuable but brief - this is a teaser for the full service.`,
      messages: [
        {
          role: 'user',
          content: `Create a 1-page competitive snapshot for: ${url}

## Company Data
Title: ${page.title}
Description: ${page.meta.description || 'N/A'}
Main Headings: ${page.headings.join(', ')}

## Search Results for "${domain} competitors"
${searchResults.map((r: SearchResult) => `- ${r.title}: ${r.snippet}`).join('\n')}

## Output Format
# Quick Competitive Snapshot: [Company Name]

## At a Glance
- **Industry Position**: [1 sentence]
- **Key Differentiator**: [1 sentence]

## Top 3 Competitors Found
1. [Competitor] - [1 sentence positioning]
2. [Competitor] - [1 sentence positioning]
3. [Competitor] - [1 sentence positioning]

## Quick Win Opportunity
[1 specific, actionable recommendation]

---
*This is a demo. Full analysis includes detailed competitor breakdowns, content gaps, SEO opportunities, and strategic recommendations.*

**Get the full analysis →** [Upgrade to paid]`,
        },
      ],
    })

    const result = response.content
      .filter((block) => block.type === 'text')
      .map((block) => (block as { type: 'text'; text: string }).text)
      .join('\n\n')

    return { success: true, result }
  } catch (error) {
    return {
      success: false,
      result: `Demo failed: ${error instanceof Error ? error.message : 'Unknown error'}`,
    }
  }
}
