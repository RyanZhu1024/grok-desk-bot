"""HTTPS client for the assistant webhook.

A 200 response only means the routine accepted the text. The spoken answer
arrives later on the ntfy topic named in the request body.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from desk_assistant.models import VALID_SOURCES, WebhookError, WebhookResult

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 8000


class WebhookClient:
    def __init__(
        self,
        url: str,
        key: str,
        *,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = (url or "").strip()
        self.key = key or ""
        self._owns_client = client is None
        if client is None:
            kwargs: dict = {
                "timeout": httpx.Timeout(timeout),
                "follow_redirects": False,
            }
            if transport is not None:
                kwargs["transport"] = transport
            client = httpx.Client(**kwargs)
        self._client = client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def send(self, text: str, source: str, reply_topic: str) -> WebhookResult:
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}, got {source!r}")
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return WebhookResult(
                ok=False,
                error=WebhookError.REJECTED,
                detail="Nothing to send.",
            )
        if len(cleaned) > MAX_TEXT_CHARS:
            return WebhookResult(
                ok=False,
                error=WebhookError.REJECTED,
                detail="That request is too long. Try a shorter one.",
            )
        if not self.url or not self.key:
            return WebhookResult(
                ok=False,
                error=WebhookError.CONFIG,
                detail=(
                    "Webhook URL and key are not set. "
                    "Edit config.yaml or set DESK_WEBHOOK_URL and DESK_WEBHOOK_KEY."
                ),
            )
        if not reply_topic.strip():
            return WebhookResult(
                ok=False,
                error=WebhookError.CONFIG,
                detail="The ntfy reply topic is empty.",
            )

        payload = {"text": cleaned, "source": source, "reply_topic": reply_topic.strip()}
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
        }
        logger.info(
            "Posting a %s request (%d chars) to %s",
            source,
            len(cleaned),
            _public_url(self.url),
        )
        try:
            response = self._client.post(self.url, json=payload, headers=headers)
        except httpx.TimeoutException:
            logger.warning("Webhook timed out: %s", _public_url(self.url))
            return WebhookResult(ok=False, error=WebhookError.NETWORK)
        except httpx.HTTPError as exc:
            logger.warning("Webhook network error: %s", exc.__class__.__name__)
            return WebhookResult(ok=False, error=WebhookError.NETWORK)
        return _interpret(response)


def _interpret(response: httpx.Response) -> WebhookResult:
    status = response.status_code
    if status == 400:
        logger.info("Webhook returned 400 (routine disabled)")
        return WebhookResult(ok=False, error=WebhookError.DISABLED)
    if status == 401:
        logger.info("Webhook returned 401 (rejected key)")
        return WebhookResult(ok=False, error=WebhookError.UNAUTHORIZED)
    if status != 200:
        logger.warning("Webhook returned HTTP %s", status)
        return WebhookResult(
            ok=False,
            error=WebhookError.HTTP,
            detail=f"The webhook returned HTTP {status}.",
        )
    try:
        body = response.json()
    except Exception:
        logger.warning("Webhook returned 200 with an unreadable body")
        return WebhookResult(
            ok=False,
            error=WebhookError.REJECTED,
            detail="The webhook returned an unreadable response.",
        )
    if not isinstance(body, dict):
        return WebhookResult(
            ok=False,
            error=WebhookError.REJECTED,
            detail="The webhook returned an unexpected response.",
        )
    if body.get("success") is True:
        run = body.get("runUuid")
        run_uuid = str(run) if run else None
        logger.info("Webhook accepted the request%s", f" ({run_uuid})" if run_uuid else "")
        return WebhookResult(ok=True, run_uuid=run_uuid)
    message = body.get("error") or body.get("message") or "The assistant did not accept the request."
    return WebhookResult(ok=False, error=WebhookError.REJECTED, detail=str(message))


def _public_url(url: str) -> str:
    """Host and path only, so a key stuck in a query string is not logged."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "(invalid url)"
    if not parsed.scheme or not parsed.netloc:
        return "(invalid url)"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
