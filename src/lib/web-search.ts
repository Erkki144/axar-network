import * as cheerio from 'cheerio'

export interface SearchResult {
  title: string
  url: string
  snippet: string
}

export interface WebPage {
  url: string
  title: string
  content: string
  headings: string[]
  meta: {
    description?: string
    keywords?: string
  }
}

export async function braveSearch(query: string, count = 5): Promise<SearchResult[]> {
  const apiKey = process.env.BRAVE_SEARCH_API_KEY

  if (!apiKey) {
    console.warn('BRAVE_SEARCH_API_KEY not set, using fallback search')
    return fallbackSearch(query, count)
  }

  try {
    const response = await fetch(
      `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${count}`,
      {
        headers: {
          'Accept': 'application/json',
          'X-Subscription-Token': apiKey,
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Brave Search error: ${response.status}`)
    }

    const data = await response.json()
    return (data.web?.results || []).map((r: { title: string; url: string; description: string }) => ({
      title: r.title,
      url: r.url,
      snippet: r.description,
    }))
  } catch (error) {
    console.error('Brave Search failed:', error)
    return fallbackSearch(query, count)
  }
}

async function fallbackSearch(query: string, count: number): Promise<SearchResult[]> {
  // DuckDuckGo HTML search as fallback
  try {
    const response = await fetch(
      `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`,
      {
        headers: {
          'User-Agent': 'Mozilla/5.0 (compatible; AXAR-Agent/1.0)',
        },
      }
    )

    const html = await response.text()
    const $ = cheerio.load(html)
    const results: SearchResult[] = []

    $('.result').slice(0, count).each((_, el) => {
      const $el = $(el)
      const title = $el.find('.result__title').text().trim()
      const url = $el.find('.result__url').text().trim()
      const snippet = $el.find('.result__snippet').text().trim()

      if (title && url) {
        results.push({
          title,
          url: url.startsWith('http') ? url : `https://${url}`,
          snippet,
        })
      }
    })

    return results
  } catch (error) {
    console.error('Fallback search failed:', error)
    return []
  }
}

export async function scrapeWebPage(url: string): Promise<WebPage | null> {
  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 10000)

    const response = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; AXAR-Agent/1.0)',
        'Accept': 'text/html',
      },
      signal: controller.signal,
    })

    clearTimeout(timeout)

    if (!response.ok) {
      return null
    }

    const html = await response.text()
    const $ = cheerio.load(html)

    // Remove scripts, styles, nav, footer, ads
    $('script, style, nav, footer, aside, .ad, .ads, .advertisement, [role="navigation"], [role="banner"]').remove()

    const title = $('title').text().trim() || $('h1').first().text().trim()
    const headings = $('h1, h2, h3').map((_, el) => $(el).text().trim()).get().slice(0, 10)

    // Get main content
    let content = ''
    const mainSelectors = ['main', 'article', '[role="main"]', '.content', '.post', '.entry']

    for (const selector of mainSelectors) {
      const mainEl = $(selector).first()
      if (mainEl.length) {
        content = mainEl.text().replace(/\s+/g, ' ').trim()
        break
      }
    }

    if (!content) {
      content = $('body').text().replace(/\s+/g, ' ').trim()
    }

    // Limit content size
    content = content.slice(0, 8000)

    return {
      url,
      title,
      content,
      headings,
      meta: {
        description: $('meta[name="description"]').attr('content'),
        keywords: $('meta[name="keywords"]').attr('content'),
      },
    }
  } catch (error) {
    console.error(`Failed to scrape ${url}:`, error)
    return null
  }
}

export async function analyzeCompetitor(url: string): Promise<{
  company: WebPage | null
  searchResults: SearchResult[]
  socialPresence: SearchResult[]
}> {
  const page = await scrapeWebPage(url)

  const domain = new URL(url).hostname.replace('www.', '')
  const companyName = page?.title?.split(/[-|–]/)[0]?.trim() || domain

  const [searchResults, socialPresence] = await Promise.all([
    braveSearch(`"${companyName}" reviews OR alternatives`, 5),
    braveSearch(`site:linkedin.com OR site:twitter.com OR site:facebook.com "${companyName}"`, 3),
  ])

  return {
    company: page,
    searchResults,
    socialPresence,
  }
}
