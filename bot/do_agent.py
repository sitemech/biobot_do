"""Client for communicating with a DigitalOcean AI Agent."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
        async with self._lock:
            response = await self._client.post(url, headers=self._headers)
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
                response = await self._client.post(
                    url,
                    headers=self._endpoint_headers,
                    json=payload,
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
            response = await self._client.post(
                url,
                headers=self._headers,
                json=payload,
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
