#!/usr/bin/env python3
import os
from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
import httpx
from aiolimiter import AsyncLimiter
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

# Rate limiter for Discogs API (60 requests per minute)
# Using aiolimiter's leaky bucket algorithm for accurate rate limiting
rate_limiter = AsyncLimiter(max_rate=60, time_period=60)

def _get_headers():
    return {"User-Agent": USER_AGENT} | (
        {"Authorization": f"Discogs token={DISCOGS_TOKEN}"} if DISCOGS_TOKEN else {}
    )


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
    Search Discogs for records. Returns basic search results. Use get_release() for detailed info.

    Args:
        query: Search query string
        n: Number of results to return (default 5, max 100)
        type: Filter by type: release, master, artist, label
        artist: Filter by artist name
        genre: Filter by genre
        year: Filter by release year
        format: Filter by format (e.g., Vinyl, CD, Cassette)
    """
    params = {k: v for k, v in {
        "q": query, "per_page": min(n, 100), "type": type,
        "artist": artist, "genre": genre, "year": year, "format": format
    }.items() if v is not None}

    async with httpx.AsyncClient() as client:
        async with rate_limiter:
            resp = await client.get(f"{BASE_URL}/database/search", params=params, headers=_get_headers())
            resp.raise_for_status()
            items = resp.json().get("results", [])[:n]

    return [{
        "release_id": int(m.group(1)) if (m := re.search(r"/release/(\d+)", item.get("uri", ""))) else None,
        "title": item.get("title"),
        "year": item.get("year"),
        "format": item.get("format"),
        "label": item.get("label"),
        "genre": item.get("genre"),
        "style": item.get("style"),
        "country": item.get("country"),
        "url": f"https://www.discogs.com{item.get('uri', '')}",
        "thumb": item.get("thumb"),
    } for item in items]


@mcp.tool()
async def get_release(release_id: int) -> dict:
    """
    Get detailed info about a specific release, including community stats and pricing.

    Args:
        release_id: The Discogs release ID
    """
    async with httpx.AsyncClient() as client:
        # Acquire rate limit tokens for both requests
        async with rate_limiter:
            async with rate_limiter:
                release_resp, stats_resp = await asyncio.gather(
                    client.get(f"{BASE_URL}/releases/{release_id}", headers=_get_headers()),
                    client.get(f"{BASE_URL}/marketplace/stats/{release_id}", headers=_get_headers()),
                    return_exceptions=True
                )
        release_resp.raise_for_status()
        data = release_resp.json()

        # Extract pricing if available
        pricing = {"lowest_price": None, "median_price": None, "highest_price": None}
        if not isinstance(stats_resp, Exception):
            try:
                stats_resp.raise_for_status()
                stats_data = stats_resp.json()
                pricing = {
                    "lowest_price": stats_data.get("lowest_price", {}).get("value"),
                    "median_price": stats_data.get("median", {}).get("value"),
                    "highest_price": stats_data.get("highest_price", {}).get("value"),
                }
            except Exception:
                pass

    community = data.get("community", {})
    rating = community.get("rating", {})

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
            "want": community.get("want"),
            "have": community.get("have"),
            "rating": rating.get("average"),
            "ratings_count": rating.get("count"),
        },
        "pricing": pricing,
        "url": data.get("uri"),
    }


# Export ASGI app for deployment
app = mcp.sse_app()

if __name__ == "__main__":
    import uvicorn
    # Get port from environment (Railway/Render set this) or default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
