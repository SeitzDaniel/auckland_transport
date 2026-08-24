"""Thin async client for the Auckland Transport API.

Every HTTP call the integration makes lives here so the call budget is easy to
audit. See ``API_NOTES.md`` for the verified behaviour of each endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    API_MAX_HOUR_RANGE,
    API_MIN_START_HOUR,
    API_REALTIME_COMBINED,
    API_ROUTES_ENDPOINT,
    API_STOPS_ENDPOINT,
    API_TIMEOUT,
    API_VERSIONS_ENDPOINT,
)

_LOGGER = logging.getLogger(__name__)


class ATApiError(Exception):
    """Raised when the Auckland Transport API cannot be used."""


class ATAuthError(ATApiError):
    """Raised when the subscription key is rejected."""


class ATRateLimitError(ATApiError):
    """Raised when the subscription quota is exhausted."""


class AucklandTransportApi:
    """Minimal wrapper around the endpoints the integration relies on."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Store the shared Home Assistant session and the subscription key."""
        self._session = session
        self._api_key = api_key

    @property
    def api_key(self) -> str:
        """Return the subscription key this client authenticates with."""
        return self._api_key

    async def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        empty_on_404: bool = False,
    ) -> Any:
        """Perform an authenticated GET and return the decoded JSON body."""
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "Accept-Encoding": "gzip",
        }
        try:
            async with asyncio.timeout(API_TIMEOUT):
                async with self._session.get(url, headers=headers, params=params) as response:
                    if response.status in (401, 403):
                        raise ATAuthError("Auckland Transport rejected the API key")
                    if response.status == 429:
                        raise ATRateLimitError("Auckland Transport API quota exceeded")
                    if response.status == 404 and empty_on_404:
                        # stoptrips answers 404 when the requested window simply
                        # contains no services, which is not an error.
                        return {"data": []}
                    if response.status != 200:
                        body = (await response.text())[:200]
                        raise ATApiError(f"HTTP {response.status} from {URL(url).path}: {body}")
                    # The GTFS endpoints answer with application/vnd.api+json, which
                    # aiohttp will not decode unless content type checking is off.
                    return await response.json(content_type=None)
        except TimeoutError as err:
            raise ATApiError(f"Timeout calling {URL(url).path}") from err
        except aiohttp.ClientError as err:
            raise ATApiError(f"Error calling {URL(url).path}: {err}") from err

    async def async_validate_key(self) -> None:
        """Raise if the configured key cannot read the API."""
        await self._get(API_VERSIONS_ENDPOINT)

    async def async_get_feed_version(self) -> str | None:
        """Return the currently published GTFS static feed version."""
        payload = await self._get(API_VERSIONS_ENDPOINT)
        for item in payload.get("data") or []:
            version = (item.get("attributes") or {}).get("feed_version")
            if version:
                return str(version)
        return None

    async def async_get_stops(self) -> list[dict[str, Any]]:
        """Return every stop in the feed (~7000 entries, ~170 KB gzipped)."""
        payload = await self._get(API_STOPS_ENDPOINT)
        return payload.get("data") or []

    async def async_get_routes(self) -> list[dict[str, Any]]:
        """Return every route in the feed, including the official line colours."""
        payload = await self._get(API_ROUTES_ENDPOINT)
        return payload.get("data") or []

    async def async_get_stop_trips(
        self, stop_id: str, date: str, start_hour: int, hour_range: int
    ) -> list[dict[str, Any]]:
        """Return the scheduled trips calling at ``stop_id``.

        Querying a parent station returns the trips of all of its platforms, with
        the platform's own ``stop_id`` on each trip.
        """
        params = {
            "filter[date]": date,
            # The API rejects start_hour 0 outright and caps hour_range at 30.
            "filter[start_hour]": str(max(API_MIN_START_HOUR, min(23, start_hour))),
            "filter[hour_range]": str(max(1, min(API_MAX_HOUR_RANGE, hour_range))),
        }
        payload = await self._get(
            f"{API_STOPS_ENDPOINT}/{stop_id}/stoptrips", params, empty_on_404=True
        )
        return payload.get("data") or []

    async def async_get_realtime(self) -> list[dict[str, Any]]:
        """Return every realtime entity in one call.

        The unfiltered combined feed carries trip updates, vehicle positions and
        service alerts together, so a single request serves every configured stop.
        """
        payload = await self._get(API_REALTIME_COMBINED)
        if payload.get("status") not in (None, "OK"):
            raise ATApiError(f"Realtime feed reported status {payload.get('status')!r}")
        return (payload.get("response") or {}).get("entity") or []
