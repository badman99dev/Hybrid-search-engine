import asyncio
import json
import os
from urllib.parse import quote_plus

import httpx
from ddgs import DDGS
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

app = FastAPI()

SERPER_PRIMARY_KEY = os.environ.get("SERPER_PRIMARY_KEY", "d4eef379dc53d1a4a1ff607618f673a8b6544ce0")
SERPER_FALLBACK_KEY = os.environ.get("SERPER_FALLBACK_KEY", "e1ee752adc11668802ff161c4c6f38f52ca52498")


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


def search_serper(query: str, max_results: int = 10, gl: str = "us") -> dict:
    try:
        url = "https://google.serper.dev/search"
        body = {"q": query, "gl": gl, "num": max_results}
        headers = {"X-API-KEY": SERPER_PRIMARY_KEY, "Content-Type": "application/json"}

        with httpx.Client(timeout=15) as client:
            resp = client.post(url, json=body, headers=headers)
            if not resp.is_success:
                headers["X-API-KEY"] = SERPER_FALLBACK_KEY
                resp = client.post(url, json=body, headers=headers)
            if not resp.is_success:
                return {"organic": [], "knowledgeGraph": None, "answerBox": None}
            return resp.json()
    except Exception:
        return {"organic": [], "knowledgeGraph": None, "answerBox": None}


def search_ddgs(query: str, max_results: int = 10, backend: str = "html") -> list:
    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results, backend=backend))
    except Exception:
        return []


def merge_results(google_data: dict, ddg_results: list, bing_results: list) -> dict:
    merged = {}
    order = []

    for item in (google_data.get("organic") or []):
        key = normalize_url(item.get("link", ""))
        if not key:
            continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "previews": [],
                "engines": [],
            }
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
            merged[key] = {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "previews": [],
                "engines": [],
            }
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
            merged[key] = {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "previews": [],
                "engines": [],
            }
            order.append(key)
        snippet = item.get("body", "")
        if snippet:
            merged[key]["previews"].append({"engine": "DDG", "snippet": snippet})
        if "ddg" not in merged[key]["engines"]:
            merged[key]["engines"].append("ddg")

    deduped_previews = []
    for key in order:
        item = merged[key]
        seen_snippets = {}
        for p in item["previews"]:
            snip = p["snippet"].strip().lower()[:100]
            if snip not in seen_snippets:
                seen_snippets[snip] = [p["engine"]]
            else:
                seen_snippets[snip].append(p["engine"])
        combined = []
        for snip, engines in seen_snippets.items():
            combined.append({"engine": "+".join(engines), "snippet": next(
                p["snippet"] for p in item["previews"] if p["snippet"].strip().lower()[:100] == snip
            )})
        item["previews"] = combined
        deduped_previews.append(item)

    return {
        "organic": deduped_previews,
        "knowledgeGraph": google_data.get("knowledgeGraph"),
        "answerBox": google_data.get("answerBox"),
        "total": len(deduped_previews),
    }


@app.get("/api/search")
async def search(
    q: str = Query(..., description="Search query"),
    max_results: int = Query(10, ge=1, le=30),
    gl: str = Query("us", description="Country code for Google geo location"),
):
    google_data, ddg_results, bing_results = await asyncio.gather(
        asyncio.to_thread(search_serper, q, max_results, gl),
        asyncio.to_thread(search_ddgs, q, max_results, "html"),
        asyncio.to_thread(search_ddgs, q, max_results, "bing"),
    )
    merged = merge_results(google_data, ddg_results, bing_results)
    return JSONResponse(content=merged)


@app.get("/")
async def root():
    return {"status": "ok", "service": "Hybrid Search Engine", "endpoints": ["/api/search"]}
