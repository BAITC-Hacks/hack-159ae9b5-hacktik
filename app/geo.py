"""Server-side address search. Browser positioning and map pins need no API key."""
import asyncio
import os
import time

import httpx
from fastapi import HTTPException, Query


def mount_geo(app):
    cache = {}
    lock = asyncio.Lock()
    last_request = 0.0

    @app.get("/api/geo/search")
    async def search(q: str = Query(min_length=3, max_length=250)):
        nonlocal last_request
        key = os.getenv("GEOAPIFY_API_KEY")
        if not key:
            raise HTTPException(503, "Поиск адресов не настроен. Выберите точку на карте или укажите координаты.")
        query = q.strip()
        async with lock:
            now = time.monotonic()
            if query in cache and now - cache[query][0] < 3600:
                return cache[query][1]
            if now - last_request < 1:
                raise HTTPException(429, "Повторите поиск через секунду")
            last_request = now
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    result = await client.get("https://api.geoapify.com/v1/geocode/search", params={
                        "text": query, "format": "json", "lang": "ru", "limit": 5, "apiKey": key})
                    result.raise_for_status()
                    rows = result.json().get("results", [])
                    data = {"items": [{"lat": row["lat"], "lng": row["lon"], "address": row["formatted"]}
                                      for row in rows], "attribution": "Geoapify / OpenStreetMap"}
            except (httpx.HTTPError, ValueError, KeyError):
                raise HTTPException(503, "Поиск адреса временно недоступен. Выберите точку на карте.") from None
            if len(cache) >= 500:
                cache.clear()
            cache[query] = (now, data)
            return data
