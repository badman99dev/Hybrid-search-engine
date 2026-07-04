# Hybrid Search Engine

Vercel-hosted meta search API that merges results from **Google (Serper)**, **DuckDuckGo (ddgs)**, and **Bing (ddgs)** in parallel.

## Features

- **3 search engines** merged in parallel (Google + DuckDuckGo + Bing)
- **Multi-page support** — fetch up to 3 pages in one request (`page=1,2,3`)
- **Smart dedup** — URL normalization + snippet similarity matching
- **Multi-engine previews** — see which engine returned each snippet (`Google+Bing+DDG`)
- **Engine selection** — use all or pick specific engines (`google`, `ddg`, `bing`)
- **Time filter** — past hour/day/week/month/year (`h`, `d`, `w`, `m`, `y`)
- **Region filter** — unified region param for all engines (`us`, `in`, `uk`, etc.)
- **7 search types** — text, images, videos, news, places, maps, shopping
- **Auth barrier** — PROTECTED by default, Bearer token required
- **Serverless** — hosted on Vercel, auto-scales, free tier

## Authentication

API is **PROTECTED by default**. All requests require `Authorization` header.

| `MODE` env | `ACCESS_KEY` env | Behavior |
|-----------|-----------------|----------|
| not set | set | PROTECTED — Bearer token required (default) |
| `PROTECTED` | set | PROTECTED — Bearer token required |
| `PROTECTED` | not set | Build fail — key required |
| `OPEN` | (ignored) | No auth, open access |

### Request with auth

```bash
curl -H "Authorization: Bearer YOUR_ACCESS_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=python"
```

## Endpoints

### `GET /api/search`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `max_results` | int | 10 | Results per engine per page (1-30) |
| `region` | string | "us" | Country code for geo location (`us`, `in`, `uk`, etc.) |
| `time` | string | null | Time filter: `h` (hour), `d` (day), `w` (week), `m` (month), `y` (year) |
| `type` | string | "search" | Search type: `search`, `images`, `videos`, `news`, `places`, `maps`, `shopping` |
| `engines` | string | "google,ddg,bing" | Comma-separated engines to use |
| `page` | string | "1" | Page number(s): `1` or `1,2,3` (max 3 pages, fetched in parallel) |

### Notes

- `type=search` uses multi-engine merge (Google + DDG + Bing)
- Other types (`images`, `videos`, `news`, `places`, `maps`, `shopping`) use Google (Serper) directly
- `time=h` (past hour) only supports Google — DDG/Bing don't support hour filter, so only Google results are returned
- `page=1,2,3` fetches 3 pages in parallel from all selected engines — up to 9 parallel calls

## Response

### Text search (`type=search`)

```json
{
  "organic": [
    {
      "title": "Example Title",
      "url": "https://example.com",
      "previews": [
        {"engine": "Google+Bing", "snippet": "Same snippet merged from both..."},
        {"engine": "DDG", "snippet": "Different snippet from DDG..."}
      ],
      "engines": ["google", "bing", "ddg"]
    }
  ],
  "knowledgeGraph": {...},
  "answerBox": {...},
  "total": 15
}
```

### Other types

Returns Serper API response directly (images, videos, news, places, maps, shopping).

## Examples

### Basic search (all engines, page 1)

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=python+programming"
```

### Multi-page (3 pages in parallel)

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=best+vpn&page=1,2,3&max_results=10"
```

### Google only, past 24 hours

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=latest+news&engines=google&time=d"
```

### DDG + Bing only, India region

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=best+vpn&engines=ddg,bing&region=in"
```

### Image search

```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://hybrid-search-engine.vercel.app/api/search?q=python+logo&type=images"
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SERPER_PRIMARY_KEY` | Yes | Serper.dev API key (Google) |
| `SERPER_FALLBACK_KEY` | Yes | Fallback Serper key |
| `ACCESS_KEY` | Yes (if PROTECTED) | Bearer token for API auth |
| `MODE` | No | `PROTECTED` (default) or `OPEN` |

## Performance

| Scenario | Parallel calls | Time |
|----------|---------------|------|
| 1 page, 3 engines | 3 | ~1.1s |
| 3 pages, 3 engines | 9 | ~2.0s |
| 1 page, 1 engine | 1 | ~1.0s |

## Tech Stack

- **[ddgs](https://github.com/deedy5/ddgs)** — DuckDuckGo + Bing metasearch library by [deedy5](https://github.com/deedy5)
- **[Serper.dev](https://serper.dev)** — Google search API
- **FastAPI** — Python web framework
- **Vercel** — Serverless hosting (Python runtime)
- **asyncio.gather** — Parallel engine fetching

## Special Thanks

- **[deedy5](https://github.com/deedy5)** — creator of [ddgs](https://github.com/deedy5/ddgs) (formerly `duckduckgo-search`) and [primp](https://github.com/deedy5/primp). Without ddgs, the multi-engine merge (DuckDuckGo + Bing) wouldn't be possible. This project heavily relies on ddgs for scraping DuckDuckGo and Bing results in parallel.
- **[Serper.dev](https://serper.dev)** — for providing a fast and reliable Google Search API.

## Keep Warm (Free)

Vercel Hobby plan supports only daily cron jobs. To keep the function warm (avoid cold starts), set up a free external ping:

1. Go to **https://cron-job.org** (free)
2. Create a job:
   - URL: `https://hybrid-search-engine.vercel.app/`
   - Schedule: every 4 minutes
3. Done — function stays warm, no cold starts

> **Note:** Hitting root `/` returns 401 (unauthorized) but **still keeps the function warm** — cold start happens at function initialization, not at response code. So no auth header needed in cron job, and no Serper/ddgs API calls are made = completely free warmup.
