import asyncio
import json
import os
import re
from urllib.parse import quote_plus

import httpx
from ddgs import DDGS
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

app = FastAPI()

SERPER_PRIMARY_KEY = os.environ.get("SERPER_PRIMARY_KEY", "d4eef379dc53d1a4a1ff607618f673a8b6544ce0")
SERPER_FALLBACK_KEY = os.environ.get("SERPER_FALLBACK_KEY", "e1ee752adc11668802ff161c4c6f38f52ca52498")

MODE = os.environ.get("MODE", "PROTECTED")
ACCESS_KEY = os.environ.get("ACCESS_KEY", None)

if MODE != "OPEN" and not ACCESS_KEY:
    raise RuntimeError("ACCESS_KEY env var is required when MODE is not OPEN")

ACCESS_KEY = ACCESS_KEY or ""


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if MODE != "OPEN":
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {ACCESS_KEY}":
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized — valid Authorization header required"},
            )
    return await call_next(request)

VALID_TYPES = ["search", "images", "videos", "news", "places", "maps", "shopping"]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip().lower()
    if url.startswith("https://"):
        url = url[8:]
    elif url.startswith("http://"):
        url = url[7:]
    if url.startswith("www."):
        url = url[4:]
    if url.endswith("/"):
        url = url[:-1]
    return url


def normalize_snippet(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d+,?\s*\d{4}\s*\.{0,3}\s*", "", text)
    text = re.sub(r"^\d+\s+(day|hour|minute|week|month|year)s?\s*ago\s*\.{0,3}\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text[:150]


def snippet_similarity(a: str, b: str) -> float:
    na, nb = normalize_snippet(a), normalize_snippet(b)
    if na == nb:
        return 1.0
    if na.startswith(nb[:80]) or nb.startswith(na[:80]):
        return 0.9
    if len(na) > 20 and len(nb) > 20:
        words_a = set(na.split())
        words_b = set(nb.split())
        return len(words_a & words_b) / max(len(words_a | words_b), 1)
    return 0.0


def dedup_previews(previews: list) -> list:
    groups = []
    for p in previews:
        matched = False
        for g in groups:
            if snippet_similarity(p["snippet"], g[2]) >= 0.75:
                g[1].append(p["engine"])
                if len(p["snippet"]) > len(g[2]):
                    g[2] = p["snippet"]
                matched = True
                break
        if not matched:
            groups.append([normalize_snippet(p["snippet"]), [p["engine"]], p["snippet"]])
    return [{"engine": "+".join(g[1]), "snippet": g[2]} for g in groups]


def serper_request(endpoint: str, query: str, gl: str = "us", num: int = 10, tbs: str = None, page: int = 1) -> dict:
    try:
        url = f"https://google.serper.dev/{endpoint}"
        body: dict = {"q": query, "gl": gl, "num": num, "page": page}
        if tbs:
            body["tbs"] = tbs
        headers = {"X-API-KEY": SERPER_PRIMARY_KEY, "Content-Type": "application/json"}
        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json=body, headers=headers)
            if not resp.is_success:
                headers["X-API-KEY"] = SERPER_FALLBACK_KEY
                resp = client.post(url, json=body, headers=headers)
            if not resp.is_success:
                return {}
            return resp.json()
    except Exception:
        return {}


def ddgs_text(query: str, max_results: int, backend: str, region: str = "us-en", timelimit: str = None, page: int = 1) -> list:
    try:
        kwargs = {"max_results": max_results, "backend": backend, "region": region, "safesearch": "off", "page": page}
        if timelimit:
            kwargs["timelimit"] = timelimit
        with DDGS() as ddgs:
            return list(ddgs.text(query, **kwargs))
    except Exception:
        return []


def merge_text_results(google_data: dict, ddg_results: list, bing_results: list) -> dict:
    merged = {}
    order = []

    for item in (google_data.get("organic") or []):
        key = normalize_url(item.get("link", ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = {"title": item.get("title", ""), "url": item.get("link", ""), "previews": [], "engines": []}
            order.append(key)
        snippet = item.get("snippet", "")
        if snippet:
            merged[key]["previews"].append({"engine": "Google", "snippet": snippet})
        if "google" not in merged[key]["engines"]:
            merged[key]["engines"].append("google")

    for item in bing_results:
        key = normalize_url(item.get("href", ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = {"title": item.get("title", ""), "url": item.get("href", ""), "previews": [], "engines": []}
            order.append(key)
        snippet = item.get("body", "")
        if snippet:
            merged[key]["previews"].append({"engine": "Bing", "snippet": snippet})
        if "bing" not in merged[key]["engines"]:
            merged[key]["engines"].append("bing")

    for item in ddg_results:
        key = normalize_url(item.get("href", ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = {"title": item.get("title", ""), "url": item.get("href", ""), "previews": [], "engines": []}
            order.append(key)
        snippet = item.get("body", "")
        if snippet:
            merged[key]["previews"].append({"engine": "DDG", "snippet": snippet})
        if "ddg" not in merged[key]["engines"]:
            merged[key]["engines"].append("ddg")

    organic = []
    for key in order:
        item = merged[key]
        item["previews"] = dedup_previews(item["previews"])
        organic.append(item)

    return {
        "organic": organic,
        "knowledgeGraph": google_data.get("knowledgeGraph"),
        "answerBox": google_data.get("answerBox"),
        "total": len(organic),
    }


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(10, ge=1, le=30),
    region: str = Query("us", description="Country code for geo location (e.g. 'us', 'in', 'uk')"),
    time: str = Query(None, description="Time filter: h (hour), d (day), w (week), m (month), y (year)"),
    type: str = Query("search", description="Search type: search, images, videos, news, places, maps, shopping"),
    engines: str = Query("google,ddg,bing", description="Comma-separated engines to use (default: all). e.g. 'google', 'ddg,bing', 'google,ddg,bing'"),
    page: str = Query("1", description="Page number(s): '1' or '1,2,3' (max 3 pages, fetched in parallel)"),
):
    search_type = type.lower().strip()
    valid_times = ["h", "d", "w", "m", "y"]
    time_filter = time.lower().strip() if time else None
    if time_filter and time_filter not in valid_times:
        return JSONResponse(content={"error": f"Invalid time '{time_filter}'. Valid: {valid_times}"}, status_code=400)

    selected_engines = [e.strip().lower() for e in engines.split(",") if e.strip()]
    valid_engines = ["google", "ddg", "bing"]
    invalid = [e for e in selected_engines if e not in valid_engines]
    if invalid:
        return JSONResponse(content={"error": f"Invalid engine(s) '{invalid}'. Valid: {valid_engines}"}, status_code=400)

    try:
        pages = [int(p.strip()) for p in page.split(",") if p.strip()]
    except ValueError:
        return JSONResponse(content={"error": f"Invalid page '{page}'. Use page numbers like '1' or '1,2,3'"}, status_code=400)
    if not pages:
        return JSONResponse(content={"error": "No valid page numbers provided"}, status_code=400)
    if len(pages) > 3:
        return JSONResponse(content={"error": f"Maximum 3 pages allowed, got {len(pages)}"}, status_code=400)
    pages = sorted(set(pages))

    serper_tbs = f"qdr:{time_filter}" if time_filter else None
    ddgs_region = f"{region}-en"
    ddgs_timelimit = time_filter if time_filter and time_filter != "h" else None

    if search_type == "search":
        use_google = "google" in selected_engines
        use_ddg = "ddg" in selected_engines and time_filter != "h"
        use_bing = "bing" in selected_engines and time_filter != "h"

        if not (use_google or use_ddg or use_bing):
            return JSONResponse(content={"error": "No valid engines selected for this time filter"}, status_code=400)

        tasks = []
        task_labels = []

        for p in pages:
            if use_google:
                tasks.append(asyncio.to_thread(serper_request, "search", q, region, max_results, serper_tbs, p))
                task_labels.append(("google", p))
            if use_ddg:
                tasks.append(asyncio.to_thread(ddgs_text, q, max_results, "html", ddgs_region, ddgs_timelimit, p))
                task_labels.append(("ddg", p))
            if use_bing:
                tasks.append(asyncio.to_thread(ddgs_text, q, max_results, "bing", ddgs_region, ddgs_timelimit, p))
                task_labels.append(("bing", p))

        results = await asyncio.gather(*tasks)

        all_google_organic = []
        all_ddg_results = []
        all_bing_results = []
        kg = None
        ab = None

        for (engine_name, _page_num), res in zip(task_labels, results):
            if engine_name == "google":
                all_google_organic.extend(res.get("organic") or [])
                if kg is None and res.get("knowledgeGraph"):
                    kg = res.get("knowledgeGraph")
                if ab is None and res.get("answerBox"):
                    ab = res.get("answerBox")
            elif engine_name == "ddg":
                all_ddg_results.extend(res)
            elif engine_name == "bing":
                all_bing_results.extend(res)

        google_data = {"organic": all_google_organic, "knowledgeGraph": kg, "answerBox": ab}
        return JSONResponse(content=merge_text_results(google_data, all_ddg_results, all_bing_results))

    elif search_type in ("images", "videos", "news", "places", "maps", "shopping"):
        data = serper_request(search_type, q, region, max_results, serper_tbs, pages[0])
        return JSONResponse(content=data)

    else:
        return JSONResponse(content={"error": f"Invalid type '{search_type}'. Valid: {VALID_TYPES}"}, status_code=400)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "Hybrid Search Engine",
        "endpoints": ["/api/search"],
        "types": VALID_TYPES,
        "params": {
            "q": "Search query (required)",
            "max_results": "1-30 (default 10)",
            "region": "us, in, uk, etc. (default us)",
            "time": "h, d, w, m, y (optional)",
            "type": "search, images, videos, news, places, maps, shopping (default search)",
            "engines": "google, ddg, bing — comma-separated (default: all)",
            "page": "1, or 1,2,3 — max 3 pages fetched in parallel (default: 1)",
        },
    }
