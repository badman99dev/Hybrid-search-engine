# Hybrid Search Engine

Vercel-hosted meta search API that merges results from Google (Serper), DuckDuckGo (ddgs), and Bing (ddgs).

## Endpoints

### `GET /api/search`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `q` | string | required | Search query |
| `max_results` | int | 10 | Results per engine (1-30) |
| `gl` | string | "us" | Google geo location |

## Response

```json
{
  "organic": [
    {
      "title": "Example Title",
      "url": "https://example.com",
      "previews": [
        {"engine": "Google+Bing", "snippet": "Same snippet from both..."},
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

## Environment Variables

- `SERPER_PRIMARY_KEY` - Serper.dev API key
- `SERPER_FALLBACK_KEY` - Fallback Serper key

## Keep Warm (Free)

Vercel Hobby plan supports only daily cron jobs. To keep the function warm (avoid cold starts), set up a free external ping:

1. Go to **https://cron-job.org** (free, no signup needed with GitHub)
2. Create a job:
   - URL: `https://YOUR_APP.vercel.app/api/search?q=warmup&max_results=1`
   - Schedule: every 4 minutes
3. Done — function stays warm, no cold starts
