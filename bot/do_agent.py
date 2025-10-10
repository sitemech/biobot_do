"""Client for communicating with a DigitalOcean AI Agent."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import httpx
import uuid

logger = logging.getLogger(__name__)


class DigitalOceanAgentError(RuntimeError):
    """Raised when the DigitalOcean AI Agent API returns an error."""


@dataclass(slots=True)
class AgentResponse:
    """Container for a response from the DigitalOcean AI Agent."""

    message: str
    raw: Dict[str, Any]


class DigitalOceanAgentClient:
    """Minimal async client for interacting with the DigitalOcean AI Agent API."""

    def __init__(
        self,
        api_key: str,
        agent_id: str,
        *,
        base_url: str = "https://api.digitalocean.com/v2/ai",
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
        # Optional direct agent endpoint mode
        agent_endpoint: Optional[str] = None,
        agent_access_key: Optional[str] = None,
        # Retry / backoff configuration
        max_retries: int = 3,
        base_backoff: float = 0.5,
        max_backoff: float = 60.0,
        # Token-bucket rate limiter (requests per second and burst)
        rate_qps: float = 5.0,
        rate_burst: int = 10,
        rate_cooldown: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._agent_id = agent_id
        self._base_url = base_url.rstrip("/")
        self._agent_endpoint = agent_endpoint.rstrip("/") if agent_endpoint else None
        self._agent_access_key = agent_access_key
        self._use_endpoint = bool(self._agent_endpoint and self._agent_access_key)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        # Retry/backoff policy
        self._max_retries = int(max_retries)
        self._base_backoff = float(base_backoff)
        self._max_backoff = float(max_backoff)
        # Token-bucket state
        self._rate_qps = float(rate_qps)
        self._rate_burst = int(rate_burst)
        self._tokens = float(self._rate_burst)
        # Use event loop time for monotonic timestamp
        self._last_refill = asyncio.get_event_loop().time()
        self._rate_lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self._default_retry_after = float(max(rate_cooldown, 0.0))

    async def close(self) -> None:
        """Close the underlying HTTP client if owned by the instance."""

        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "DigitalOceanAgentClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        await self.close()

    async def create_session(self) -> str:
        """Create a fresh conversation session for the configured agent."""
        if self._use_endpoint:
            # Endpoint mode does not require creating a session; return a
            # synthetic session id so handlers can store something.
            sid = f"endpoint-{uuid.uuid4().hex}"
            logger.debug("Using agent endpoint mode, generated session id %s", sid)
            return sid

        url = f"{self._base_url}/agents/{self._agent_id}/sessions"
        logger.debug("Creating new DigitalOcean AI Agent session at %s", url)
        # perform request with retry behaviour
        async with self._lock:
            response = await self._request_with_retries("POST", url, headers=self._headers)
        data = self._handle_response(response)
        session_id = (
            data.get("session", {}).get("id")
            or data.get("id")
            or data.get("session_id")
        )
        if not session_id:
            raise DigitalOceanAgentError(
                "DigitalOcean API response did not include a session identifier"
            )
        return str(session_id)

    async def send_message(self, session_id: str, message: str) -> AgentResponse:
        """Send a user message to the agent and return the assistant reply."""
        if self._use_endpoint:
            url = f"{self._agent_endpoint}/api/v1/chat/completions"
            payload = {
                "messages": [{"role": "user", "content": message}],
                "stream": False,
                "include_retrieval_info": False,
                "include_functions_info": False,
                "include_guardrails_info": False,
            }
            logger.debug("Sending message to agent endpoint %s: %s", url, message)
            async with self._lock:
                response = await self._request_with_retries(
                    "POST", url, headers=self._endpoint_headers, json=payload
                )
            data = self._handle_response(response)
            # Try several extraction heuristics (OpenAI-like or agent response)
            reply = self._extract_endpoint_reply_text(data)
            return AgentResponse(message=reply, raw=data)

        url = f"{self._base_url}/sessions/{session_id}/messages"
        payload = {"role": "user", "content": message}
        logger.debug(
            "Sending message to session %s via %s: %s", session_id, url, message
        )
        async with self._lock:
            response = await self._request_with_retries(
                "POST", url, headers=self._headers, json=payload
            )
        data = self._handle_response(response)
        reply = self._extract_reply_text(data)
        return AgentResponse(message=reply, raw=data)

    def _handle_response(self, response: httpx.Response) -> Dict[str, Any]:
        """Validate the HTTP response and return the decoded JSON body."""

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network errors
            detail = self._safe_json(response)
            logger.error(
                "DigitalOcean API returned status %s: %s",
                response.status_code,
                detail,
            )
            raise DigitalOceanAgentError(
                f"DigitalOcean API returned {response.status_code}: {detail}"
            ) from exc
        return self._safe_json(response)

    def _safe_json(self, response: httpx.Response) -> Dict[str, Any]:
        try:
            return response.json()
        except ValueError:  # pragma: no cover - depends on API behaviour
            return {"raw_text": response.text}

    async def _sleep_backoff(self, attempt: int, retry_after: Optional[float] = None) -> None:
        """Compute backoff sleep (with jitter) for given attempt; respect Retry-After if provided."""
        import random

        if retry_after is not None and retry_after > 0:
            to_sleep = min(retry_after, self._max_backoff)
        else:
            to_sleep = min(self._base_backoff * (2 ** attempt), self._max_backoff)
        # jitter
        jitter = to_sleep * 0.1 * (random.random())
        to_sleep = to_sleep + jitter
        logger.info("Backing off for %.2fs before retrying (attempt %d)", to_sleep, attempt)
        await asyncio.sleep(to_sleep)

    async def _request_with_retries(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Perform HTTP request with retries on 429/429-like responses.

        Respects `Retry-After` header when present and uses exponential backoff with jitter.
        """
        last_exc: Optional[Exception] = None
        for attempt in range(0, max(1, self._max_retries) + 1):
            # Acquire token from token-bucket limiter before each attempt
            await self._acquire_token()
            try:
                resp = await self._client.request(method, url, **kwargs)
                # If not a 429, return immediately (other errors handled later)
                if resp.status_code != 429:
                    return resp

                # Handle 429: try to read Retry-After header/body hints and register cooldown
                retry_after = self._parse_retry_after_header(resp)
                detail = self._safe_json(resp)
                if retry_after is None:
                    retry_after = self._extract_retry_after_from_body(detail)
                await self._register_cooldown(retry_after)
                logger.warning(
                    "Received 429 from DigitalOcean agent endpoint (attempt %d/%d): %s",
                    attempt + 1,
                    self._max_retries,
                    detail,
                )
                if attempt >= self._max_retries:
                    return resp
                await self._sleep_backoff(attempt + 1, retry_after=retry_after)
                continue
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.exception("HTTP error during request to %s: %s", url, exc)
                # for retriable transport errors, backoff and retry
                if attempt >= self._max_retries:
                    raise
                await self._sleep_backoff(attempt + 1, retry_after=None)
                continue

        if last_exc:
            raise last_exc
        raise RuntimeError("Failed to complete request with retries")

    async def _acquire_token(self) -> None:
        """Acquire a token from the token-bucket. Wait until a token is available."""

        loop = asyncio.get_event_loop()
        while True:
            async with self._rate_lock:
                now = loop.time()

                if now < self._cooldown_until:
                    wait_seconds = self._cooldown_until - now
                else:
                    elapsed = max(0.0, now - self._last_refill)
                    if elapsed > 0:
                        refill = elapsed * self._rate_qps
                        if refill > 0:
                            self._tokens = min(self._rate_burst, self._tokens + refill)
                        self._last_refill = now

                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return

                    needed = 1.0 - self._tokens
                    wait_seconds = (
                        needed / self._rate_qps if self._rate_qps > 0 else 1.0
                    )

            await asyncio.sleep(max(wait_seconds, 0.01))

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def _endpoint_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._agent_access_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _extract_endpoint_reply_text(self, data: Dict[str, Any]) -> str:
        # OpenAI-like response: data.choices[0].message.content
        try:
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                # message.content
                msg = first.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        return content
                # text fallback
                text = first.get("text")
                if isinstance(text, str):
                    return text
        except Exception:
            pass
        # Fallback to previous extraction logic
        return self._extract_reply_text(data)

    @staticmethod
    def _extract_reply_text(data: Dict[str, Any]) -> str:
        """Extract reply text from a DigitalOcean API payload."""

        possible_paths = [
            ("message", "content"),
            ("response", "output"),
            ("response", "output_text"),
            ("data", "message", "content"),
        ]
        for path in possible_paths:
            node: Any = data
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    break
            else:
                if isinstance(node, str):
                    return node
        logger.warning("Falling back to raw response for reply text: %s", data)
        return str(data)

    def _parse_retry_after_header(self, response: httpx.Response) -> Optional[float]:
        header_value = response.headers.get("Retry-After")
        if not header_value:
            return None
        try:
            value = float(header_value)
            return max(0.0, value)
        except (TypeError, ValueError):
            try:
                dt = parsedate_to_datetime(header_value)
            except (TypeError, ValueError, OverflowError):
                return None
            if dt is None:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = (dt - now).total_seconds()
            return max(0.0, delta)

    def _extract_retry_after_from_body(self, data: Dict[str, Any]) -> Optional[float]:
        candidates = [
            ("retry_after",),
            ("retryAfter",),
            ("error", "retry_after"),
            ("error", "retryAfter"),
            ("meta", "retry_after"),
        ]

        for path in candidates:
            node: Any = data
            for key in path:
                if isinstance(node, dict) and key in node:
                    node = node[key]
                else:
                    break
            else:
                try:
                    value = float(node)
                except (TypeError, ValueError):
                    continue
                return max(0.0, value)
        return None

    async def _register_cooldown(self, retry_after: Optional[float]) -> None:
        wait_value: Any
        if retry_after in (None, 0, "0"):
            wait_value = self._default_retry_after
        else:
            wait_value = retry_after
        try:
            wait = float(wait_value)
        except (TypeError, ValueError):
            wait = self._default_retry_after
        if wait <= 0:
            return
        loop = asyncio.get_event_loop()
        now = loop.time()
        async with self._rate_lock:
            target = now + wait
            if target > self._cooldown_until:
                self._cooldown_until = target
            self._last_refill = now
            # Drop available tokens so that the next request waits until cooldown expires
            self._tokens = 0.0
