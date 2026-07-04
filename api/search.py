import asyncio
import json
import os
import re
from urllib.parse import quote_plus

import httpx
from ddgs import DDGS
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI()

SERPER_PRIMARY_KEY = os.environ.get("SERPER_PRIMARY_KEY", "d4eef379dc53d1a4a1ff607618f673a8b6544ce0")
SERPER_FALLBACK_KEY = os.environ.get("SERPER_FALLBACK_KEY", "e1ee752adc11668802ff161c4c6f38f52ca52498")

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


def serper_request(endpoint: str, query: str, gl: str = "us", num: int = 10) -> dict:
    try:
        url = f"https://google.serper.dev/{endpoint}"
        body = {"q": query, "gl": gl, "num": num}
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


def ddgs_text(query: str, max_results: int, backend: str) -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results, backend=backend))
    except Exception:
        return []


def ddgs_images(query: str, max_results: int) -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.images(query, max_results=max_results))
    except Exception:
        return []


def ddgs_videos(query: str, max_results: int) -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.videos(query, max_results=max_results))
    except Exception:
        return []


def ddgs_news(query: str, max_results: int) -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.news(query, max_results=max_results))
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


def merge_image_results(google_data: dict, ddg_results: list) -> dict:
    merged = {}
    order = []

    for item in (google_data.get("images") or []):
        url = item.get("imageUrl", "")
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "imageUrl": url,
                "thumbnailUrl": item.get("thumbnailUrl", ""),
                "source": item.get("source", ""),
                "domain": item.get("domain", ""),
                "imageWidth": item.get("imageWidth", ""),
                "imageHeight": item.get("imageHeight", ""),
                "engines": [],
            }
            order.append(key)
        if "google" not in merged[key]["engines"]:
            merged[key]["engines"].append("google")

    for item in ddg_results:
        url = item.get("image", "")
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "imageUrl": url,
                "thumbnailUrl": item.get("thumbnail", ""),
                "source": item.get("source", ""),
                "domain": "",
                "imageWidth": str(item.get("width", "")),
                "imageHeight": str(item.get("height", "")),
                "engines": [],
            }
            order.append(key)
        if "ddg" not in merged[key]["engines"]:
            merged[key]["engines"].append("ddg")

    images = [merged[key] for key in order]
    return {"images": images, "total": len(images)}


def merge_video_results(google_data: dict, ddg_results: list) -> dict:
    merged = {}
    order = []

    for item in (google_data.get("videos") or []):
        url = item.get("link", item.get("url", ""))
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "duration": item.get("duration", ""),
                "imageUrl": item.get("imageUrl", ""),
                "engines": [],
            }
            order.append(key)
        if "google" not in merged[key]["engines"]:
            merged[key]["engines"].append("google")

    for item in ddg_results:
        url = item.get("embed_url", item.get("content", ""))
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("description", ""),
                "source": item.get("publisher", ""),
                "duration": item.get("duration", ""),
                "imageUrl": "",
                "engines": [],
            }
            order.append(key)
        if "ddg" not in merged[key]["engines"]:
            merged[key]["engines"].append("ddg")

    videos = [merged[key] for key in order]
    return {"videos": videos, "total": len(videos)}


def merge_news_results(google_data: dict, ddg_results: list) -> dict:
    merged = {}
    order = []

    for item in (google_data.get("news") or []):
        url = item.get("link", "")
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "imageUrl": item.get("imageUrl", ""),
                "engines": [],
            }
            order.append(key)
        if "google" not in merged[key]["engines"]:
            merged[key]["engines"].append("google")

    for item in ddg_results:
        url = item.get("url", "")
        key = normalize_url(url)
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("body", ""),
                "source": item.get("source", ""),
                "date": item.get("date", ""),
                "imageUrl": item.get("image", ""),
                "engines": [],
            }
            order.append(key)
        if "ddg" not in merged[key]["engines"]:
            merged[key]["engines"].append("ddg")

    news = [merged[key] for key in order]
    return {"news": news, "total": len(news)}


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(10, ge=1, le=30),
    gl: str = Query("us", description="Country code for Google geo location"),
    type: str = Query("search", description="Search type: search, images, videos, news, places, maps, shopping"),
):
    search_type = type.lower().strip()

    if search_type == "search":
        google_data, ddg_results, bing_results = await asyncio.gather(
            asyncio.to_thread(serper_request, "search", q, gl, max_results),
            asyncio.to_thread(ddgs_text, q, max_results, "html"),
            asyncio.to_thread(ddgs_text, q, max_results, "bing"),
        )
        return JSONResponse(content=merge_text_results(google_data, ddg_results, bing_results))

    elif search_type == "images":
        google_data, ddg_results = await asyncio.gather(
            asyncio.to_thread(serper_request, "images", q, gl, max_results),
            asyncio.to_thread(ddgs_images, q, max_results),
        )
        return JSONResponse(content=merge_image_results(google_data, ddg_results))

    elif search_type == "videos":
        google_data, ddg_results = await asyncio.gather(
            asyncio.to_thread(serper_request, "videos", q, gl, max_results),
            asyncio.to_thread(ddgs_videos, q, max_results),
        )
        return JSONResponse(content=merge_video_results(google_data, ddg_results))

    elif search_type == "news":
        google_data, ddg_results = await asyncio.gather(
            asyncio.to_thread(serper_request, "news", q, gl, max_results),
            asyncio.to_thread(ddgs_news, q, max_results),
        )
        return JSONResponse(content=merge_news_results(google_data, ddg_results))

    elif search_type in ("places", "maps", "shopping"):
        data = serper_request(search_type, q, gl, max_results)
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
    }
