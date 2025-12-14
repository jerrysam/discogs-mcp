#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Disable DNS rebinding protection for cloud deployment
# (Render's proxy handles host validation)
transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=False
)

mcp = FastMCP("discogs", transport_security=transport_security)

DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
BASE_URL = "https://api.discogs.com"
USER_AGENT = "DiscogsMCP/1.0"


async def _fetch_release_stats(client: httpx.AsyncClient, headers: dict, release_id: int) -> dict:
    """Fetch community stats for a single release."""
    try:
        resp = await client.get(f"{BASE_URL}/releases/{release_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        community = data.get("community", {})
        return {
            "want": community.get("want"),
            "have": community.get("have"),
            "rating": community.get("rating", {}).get("average"),
        }
    except Exception:
        return {"want": None, "have": None, "rating": None}


@mcp.tool()
async def search_records(
    query: str,
    n: int = 5,
    type: str | None = None,
    artist: str | None = None,
    genre: str | None = None,
    year: str | None = None,
    format: str | None = None,
) -> list[dict]:
    """
    Search Discogs for records. Returns results with community stats (wants/haves/rating).

    Args:
        query: Search query string
        n: Number of results to return (default 5, max 100)
        type: Filter by type: release, master, artist, label
        artist: Filter by artist name
        genre: Filter by genre
        year: Filter by release year
        format: Filter by format (e.g., Vinyl, CD, Cassette)
    """
    params = {"q": query, "per_page": min(n, 100)}
    if type:
        params["type"] = type
    if artist:
        params["artist"] = artist
    if genre:
        params["genre"] = genre
    if year:
        params["year"] = year
    if format:
        params["format"] = format

    headers = {"User-Agent": USER_AGENT}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BASE_URL}/database/search",
            params=params,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("results", [])[:n]

        # Extract release IDs and fetch stats in parallel
        release_ids = []
        for item in items:
            uri = item.get("uri", "")
            match = re.search(r"/release/(\d+)", uri)
            if match:
                release_ids.append(int(match.group(1)))
            else:
                release_ids.append(None)

        # Fetch community stats for releases in parallel
        stats_tasks = []
        for rid in release_ids:
            if rid:
                stats_tasks.append(_fetch_release_stats(client, headers, rid))
            else:
                stats_tasks.append(asyncio.coroutine(lambda: {"want": None, "have": None, "rating": None})())

        stats_list = await asyncio.gather(*stats_tasks)

    results = []
    for item, stats in zip(items, stats_list):
        results.append({
            "title": item.get("title"),
            "year": item.get("year"),
            "format": item.get("format"),
            "label": item.get("label"),
            "genre": item.get("genre"),
            "style": item.get("style"),
            "country": item.get("country"),
            "url": f"https://www.discogs.com{item.get('uri', '')}",
            "thumb": item.get("thumb"),
            "want": stats.get("want"),
            "have": stats.get("have"),
            "rating": stats.get("rating"),
        })
    return results


@mcp.tool()
async def get_release(release_id: int) -> dict:
    """
    Get detailed info about a specific release, including community stats (wants/haves).

    Args:
        release_id: The Discogs release ID
    """
    headers = {"User-Agent": USER_AGENT}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BASE_URL}/releases/{release_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return {
        "title": data.get("title"),
        "artists": [a.get("name") for a in data.get("artists", [])],
        "year": data.get("year"),
        "formats": data.get("formats"),
        "labels": [l.get("name") for l in data.get("labels", [])],
        "genres": data.get("genres"),
        "styles": data.get("styles"),
        "country": data.get("country"),
        "tracklist": [{"position": t.get("position"), "title": t.get("title"), "duration": t.get("duration")} for t in data.get("tracklist", [])],
        "community": {
            "want": data.get("community", {}).get("want"),
            "have": data.get("community", {}).get("have"),
            "rating": data.get("community", {}).get("rating", {}).get("average"),
            "ratings_count": data.get("community", {}).get("rating", {}).get("count"),
        },
        "url": data.get("uri"),
    }


# Export ASGI app for deployment
app = mcp.sse_app()

if __name__ == "__main__":
    import uvicorn
    # Get port from environment (Railway/Render set this) or default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
