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

    results = []
    for item in items:
        # Extract release ID from URI
        release_id = None
        uri = item.get("uri", "")
        match = re.search(r"/release/(\d+)", uri)
        if match:
            release_id = int(match.group(1))

        results.append({
            "release_id": release_id,
            "title": item.get("title"),
            "year": item.get("year"),
            "format": item.get("format"),
            "label": item.get("label"),
            "genre": item.get("genre"),
            "style": item.get("style"),
            "country": item.get("country"),
            "url": f"https://www.discogs.com{item.get('uri', '')}",
            "thumb": item.get("thumb"),
        })
    return results


@mcp.tool()
async def get_release(release_id: int) -> dict:
    """
    Get detailed info about a specific release, including community stats and pricing.

    Args:
        release_id: The Discogs release ID
    """
    headers = {"User-Agent": USER_AGENT}
    if DISCOGS_TOKEN:
        headers["Authorization"] = f"Discogs token={DISCOGS_TOKEN}"

    async with httpx.AsyncClient() as client:
        # Fetch release details and marketplace stats in parallel
        release_resp, stats_resp = await asyncio.gather(
            client.get(f"{BASE_URL}/releases/{release_id}", headers=headers),
            client.get(f"{BASE_URL}/marketplace/stats/{release_id}", headers=headers),
            return_exceptions=True
        )

        release_resp.raise_for_status()
        data = release_resp.json()

        # Get pricing data if available
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
