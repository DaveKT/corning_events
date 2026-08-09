"""Shared HTTP client.

Every request identifies itself and paces itself. Spec section 11 asks for a
descriptive User-Agent with a contact address, backing off on errors, and
caching aggressively, on the grounds that most of these pages change once a day
at most and several are run by small organizations.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import requests

from . import config


class FetchError(Exception):
    """A request failed after exhausting retries.

    Source modules let this propagate. main.py records the failure and leaves
    that source's previous events untouched, so a transient outage can never be
    mistaken for every event being cancelled at once.
    """


class Fetcher:
    """A requests session with a User-Agent, retries and per-host pacing.

    One instance is shared across a whole run so that pacing is applied across
    sources that happen to hit the same host.
    """

    def __init__(
        self,
        user_agent: str = config.USER_AGENT,
        timeout: float = config.HTTP_TIMEOUT_SECONDS,
        max_retries: int = config.HTTP_MAX_RETRIES,
        backoff: float = config.HTTP_BACKOFF_SECONDS,
        pace: float = config.HTTP_INTER_REQUEST_SECONDS,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.pace = pace
        self._last_request: dict[str, float] = {}

    # -- core ---------------------------------------------------------------

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET with retries on transport errors and 5xx responses.

        A 4xx is not retried: it means the request itself is wrong, and
        hammering a small site with it would be rude as well as useless.
        """
        kwargs.setdefault("timeout", self.timeout)
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            self._wait_for_host(url)
            try:
                response = self.session.get(url, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response
                if response.status_code < 500:
                    raise FetchError(
                        f"{response.status_code} {response.reason} for {url}"
                    )
                last_error = FetchError(
                    f"{response.status_code} {response.reason} for {url}"
                )

            if attempt < self.max_retries:
                time.sleep(self.backoff * attempt)

        raise FetchError(f"giving up on {url} after {self.max_retries} attempts: {last_error}")

    def _wait_for_host(self, url: str) -> None:
        host = urlsplit(url).netloc
        previous = self._last_request.get(host)
        if previous is not None:
            elapsed = time.monotonic() - previous
            if elapsed < self.pace:
                time.sleep(self.pace - elapsed)
        self._last_request[host] = time.monotonic()

    # -- conveniences -------------------------------------------------------

    def text(self, url: str, **kwargs) -> str:
        response = self.get(url, **kwargs)
        # Several of these sites omit a charset, and requests then guesses
        # ISO-8859-1, which mangles the curly quotes and accents that event
        # titles are full of.
        if response.encoding is None or "charset" not in (
            response.headers.get("content-type", "").lower()
        ):
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text

    def bytes(self, url: str, **kwargs) -> bytes:
        return self.get(url, **kwargs).content

    def json(self, url: str, **kwargs):
        response = self.get(url, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"response from {url} is not JSON: {exc}") from exc

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
