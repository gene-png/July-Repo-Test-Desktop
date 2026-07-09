"""LLM client - the ONLY path that calls an external AI provider.

Master Spec §4.4: provider env-configurable, never hardcoded. §12: every
call MUST pass through the redactor first. AI Prompt §6.13 + §6.14
reinforce both.

Two modes:
  fixture - canned, deterministic responses. Tests + offline dev use this.
  live    - real provider call. Production default for v1 is Anthropic.

The client's `invoke(...)` method:
  1. Redacts the input payload via app.ai.redact.redact_payload.
  2. Writes an `llm_calls` row with status=running BEFORE the provider
     call so a crash mid-call still leaves a record.
  3. Calls the provider (fixture or live).
  4. Updates the llm_calls row with status=completed | failed plus
     token counts + duration + redacted_counts.
  5. Returns the provider response.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.redact import RedactionMode, redact_payload
from app.config import Settings, get_settings
from app.logging import correlation_id_var, get_logger
from app.models.llm_call import LLMCall, LLMCallMode, LLMCallStatus

_log = get_logger(__name__)

# H-6: the audit action recorded when an admin acknowledges the redaction
# preview for a client. A live (non-preview) AI run for that client is gated on
# the existence of such a row.
AI_PREVIEW_ACK_ACTION = "ai_preview_ack"


def has_preview_ack(db: Session, client_id: uuid.UUID) -> bool:
    """True when a redaction-preview acknowledgment exists for this client (H-6).

    Queried on the caller's session; matches any audit row whose action is
    ``ai_preview_ack`` and whose target is the client. One ack unlocks all live
    runs for that client (a per-client, not per-run, gate).
    """
    from app.models.audit_entry import AuditEntry

    return (
        db.execute(
            select(AuditEntry.id)
            .where(
                AuditEntry.action == AI_PREVIEW_ACK_ACTION,
                AuditEntry.target_id == client_id,
            )
            .limit(1)
        ).first()
        is not None
    )


# Default output-token ceiling for jobs that don't override it (Task S1-A A-3).
# Large jobs (the full ATT&CK map, the full CSF playbook) pass max_tokens=128000
# explicitly; everything else stops at end_turn well under this bound.
DEFAULT_MAX_TOKENS = 16000


class LLMConfigurationError(RuntimeError):
    """Live LLM mode is selected but the provider cannot be built.

    Carries a machine-readable ``reason`` alongside the human ``message`` so the
    route layer can surface the existing {reason, message} typed-error pattern
    (mapped to 503 at the ai-status endpoint) instead of a bare RuntimeError.
    Raised eagerly when the provider is built (at boot / first use) so a
    misconfigured live deployment fails loudly rather than on the first AI call.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class LLMTimeoutError(RuntimeError):
    """The provider call exceeded the whole-call deadline (Task S2-A E-1).

    Raised BEFORE any result is applied, so a timed-out run leaves no state
    change. The route layer / shared exception handler maps it to 504 with the
    user-facing message "the AI call timed out; nothing was changed".
    """

    def __init__(self, timeout_seconds: int) -> None:
        super().__init__(f"AI call exceeded the {timeout_seconds}s deadline")
        self.timeout_seconds = timeout_seconds


def anthropic_sdk_available() -> bool:
    """True when the `anthropic` SDK can be imported (no import side effects)."""
    import importlib.util

    return importlib.util.find_spec("anthropic") is not None


class LLMResponse:
    """Provider response container. Token counts may be None if the provider
    didn't report them (fixture mode supplies them; some providers don't)."""

    __slots__ = ("content", "input_tokens", "output_tokens")

    def __init__(
        self,
        content: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Run the prompt + payload through the provider. Synchronous; the
        caller is on a Celery worker for anything that's not interactive.

        `model`/`max_tokens` are optional per-job overrides; None inherits the
        provider's configured model and the DEFAULT_MAX_TOKENS ceiling."""
        ...


class FixtureProvider:
    """Deterministic canned responses for tests + offline dev.

    A fixture is registered per `purpose`. If the purpose isn't registered,
    `complete()` raises `KeyError` so a test that forgot to register a
    fixture fails loudly rather than silently calling out to the real
    provider.
    """

    name = "fixture"

    def __init__(self, model: str = "fixture-model-1") -> None:
        self.model = model
        self._fixtures: dict[str, Callable[[dict[str, Any]], LLMResponse]] = {}

    def register(self, purpose: str, fn: Callable[[dict[str, Any]], LLMResponse]) -> None:
        self._fixtures[purpose] = fn

    def register_static(self, purpose: str, response: LLMResponse) -> None:
        self.register(purpose, lambda _payload: response)

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        purpose = payload.get("__purpose__") or "default"
        if purpose not in self._fixtures and "default" not in self._fixtures:
            raise KeyError(
                f"No fixture registered for purpose={purpose!r}. Did you forget "
                "to call FixtureProvider.register()?"
            )
        fn = self._fixtures.get(purpose) or self._fixtures["default"]
        return fn(payload)


class AnthropicProvider:
    """Live Anthropic Claude provider.

    boto3 / anthropic SDKs are heavy and the test runs never hit them, so
    the SDK is imported lazily on first call.
    """

    name = "anthropic"

    def __init__(self, *, model: str, api_key: str) -> None:
        if not api_key:
            raise LLMConfigurationError(
                "missing_api_key",
                "ANTHROPIC_API_KEY is not set. Either set it in .env or switch "
                "SHIELD_LLM_MODE to 'fixture'.",
            )
        self.model = model
        self._api_key = api_key
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        if self._client is None:
            from anthropic import Anthropic

            # We stream every completion (see complete()), so the timeout is the
            # per-read gap between streamed events, not the whole-response budget
            # — streamed events arrive continuously, so a long generation never
            # trips it. 120s of headroom covers connection setup + first token; a
            # couple of retries recover a transient connection blip.
            self._client = Anthropic(
                api_key=self._api_key,
                max_retries=2,
                timeout=120.0,
            )
        return self._client

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        client = self._ensure_client()
        # Payload is sent as JSON inside the user message. The redactor has
        # already run upstream, so this content is safe to egress.
        import json

        # STREAM the response. A large job (e.g. the full 600+ technique MITRE
        # ATT&CK map) needs a big max_tokens, and a non-streaming request that
        # size is refused/dropped: the SDK estimates it may exceed the ~10 minute
        # non-streaming ceiling, and long-lived idle sockets get closed by the
        # server ("APIConnectionError: server disconnected"). Streaming keeps the
        # connection alive with continuous events and has no 10-minute cap, so a
        # single large call completes reliably. Large jobs pass max_tokens=128000
        # (the model's max output) so the full ATT&CK map (~65K tokens even when
        # terse) never truncates mid-JSON; smaller jobs inherit DEFAULT_MAX_TOKENS
        # and stop at end_turn long before. `model` likewise overrides per job.
        with client.messages.stream(
            model=model or self.model,
            max_tokens=max_tokens or DEFAULT_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "text", "text": json.dumps(payload)},
                    ],
                }
            ],
        ) as stream:
            msg = stream.get_final_message()
        # `msg.content` is a list of blocks; gather the text blocks.
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        input_tokens = getattr(getattr(msg, "usage", None), "input_tokens", None)
        output_tokens = getattr(getattr(msg, "usage", None), "output_tokens", None)
        return LLMResponse(text, input_tokens, output_tokens)


def _build_provider(settings: Settings) -> LLMProvider:
    if settings.shield_llm_mode == "fixture":
        return FixtureProvider(model=settings.shield_llm_model)
    if settings.shield_llm_provider == "anthropic":
        # Live mode: fail loudly and early (Task S1-A A-1). Eagerly import the
        # SDK and verify the key here so a misconfigured live deployment raises
        # the typed configuration error at boot / first use rather than on the
        # first AI call deep inside a Celery task.
        if not anthropic_sdk_available():
            raise LLMConfigurationError(
                "sdk_unavailable",
                "Live LLM mode requires the 'anthropic' SDK, which is not "
                "importable. Install it or set SHIELD_LLM_MODE=fixture.",
            )
        if not settings.anthropic_api_key:
            raise LLMConfigurationError(
                "missing_api_key",
                "Live LLM mode is on but ANTHROPIC_API_KEY is not set. Set it in "
                ".env or switch SHIELD_LLM_MODE to 'fixture'.",
            )
        return AnthropicProvider(
            model=settings.shield_llm_model,
            api_key=settings.anthropic_api_key,
        )
    raise LLMConfigurationError(
        "provider_unsupported",
        f"LLM provider {settings.shield_llm_provider!r} is not implemented in v1. "
        "Set SHIELD_LLM_PROVIDER=anthropic or SHIELD_LLM_MODE=fixture.",
    )


class LLMClient:
    """The blessed surface for AI calls. Routes never construct a provider
    directly; they go through `LLMClient.invoke(...)`."""

    def __init__(self, provider: LLMProvider, settings: Settings | None = None) -> None:
        self.provider = provider
        self._settings = settings or get_settings()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        s = settings or get_settings()
        provider = _build_provider(s)
        if isinstance(provider, FixtureProvider):
            # Fixture mode must actually simulate (E-5): register the
            # input-grounded runtime fixtures so Run AI works in demo stacks
            # instead of 500ing with "No fixture registered". Imported lazily
            # to avoid a module cycle (demo_fixtures imports FixtureProvider).
            from app.ai.demo_fixtures import register_demo_fixtures

            register_demo_fixtures(provider)
        return cls(provider, s)

    @property
    def mode(self) -> LLMMode:
        """The configured LLM mode ("fixture" | "live"). Surfaced on run-ai
        responses (E-5) so the UI can badge simulated output, and used to gate
        the H-6 live-run acknowledgment."""
        return self._settings.shield_llm_mode

    def preview(
        self,
        *,
        purpose: str,
        inputs: dict[str, Any],
        redaction_mode: RedactionMode | None = None,
        client_org_name: str | None = None,
        name_hints: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """H-6 redaction preview: run the SAME redaction ``invoke`` would run and
        return the redacted payload + removed-item counts WITHOUT calling the
        provider and WITHOUT writing an llm_calls row. The ``purpose`` is accepted
        for symmetry with ``invoke`` but does not affect redaction."""
        mode = redaction_mode or self._settings.shield_redaction_mode  # type: ignore[assignment]
        cleaned_payload, removed_counts = redact_payload(
            inputs,
            mode=mode,
            client_org_name=client_org_name,
            name_hints=name_hints,
        )
        return {"redacted_payload": cleaned_payload, "redaction_summary": removed_counts}

    def invoke(
        self,
        db: Session,
        *,
        purpose: str,
        prompt: str,
        payload: dict[str, Any],
        requested_by: uuid.UUID,
        service_id: uuid.UUID | None = None,
        client_id: uuid.UUID | None = None,
        prompt_version: str = "v1",
        redaction_mode: RedactionMode | None = None,
        client_org_name: str | None = None,
        name_hints: tuple[str, ...] = (),
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[LLMResponse, LLMCall]:
        """Redact, write the llm_calls row, call the provider, finalize the row.

        `model`/`max_tokens` are optional per-job overrides threaded from the
        AIJob; None inherits the provider's configured model / DEFAULT_MAX_TOKENS.
        `client_id` (H-5) is the tenant the call is billed to; captured on the
        row for per-client usage aggregation.
        """
        mode = redaction_mode or self._settings.shield_redaction_mode  # type: ignore[assignment]
        cleaned_payload, removed_counts = redact_payload(
            payload,
            mode=mode,
            client_org_name=client_org_name,
            name_hints=name_hints,
        )

        call_mode: LLMCallMode = (
            LLMCallMode.FIXTURE if self._settings.shield_llm_mode == "fixture" else LLMCallMode.LIVE
        )

        # E-2: the audit row lives in its OWN short-lived session on the same
        # engine, committed independently of the request transaction. A crash or
        # rollback of the caller's transaction (including an E-1 timeout that
        # abandons the request session) still leaves a durable llm_calls record.
        # We bind to db.get_bind() so it targets the same database as the caller
        # without needing the request connection (which E-1 releases during the
        # provider call).
        audit_db = Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False, future=True)
        row = LLMCall(
            service_id=service_id,
            client_id=client_id,
            purpose=purpose,
            prompt_version=prompt_version,
            provider=self.provider.name,
            model=model or self.provider.model,
            mode=call_mode,
            status=LLMCallStatus.RUNNING,
            requested_by=requested_by,
            redacted_counts=removed_counts or None,
            correlation_id=correlation_id_var.get(),
        )
        audit_db.add(row)
        audit_db.commit()  # RUNNING is durable before the provider call.

        # Pass the purpose into the fixture so tests can register per-purpose
        # responses. Real providers ignore it.
        send_payload = {**cleaned_payload, "__purpose__": purpose}

        from app.models._common import utcnow as _utcnow

        started = time.monotonic()
        try:
            response = self._complete_with_deadline(
                prompt, send_payload, model=model, max_tokens=max_tokens
            )
        except LLMTimeoutError as exc:
            row.status = LLMCallStatus.FAILED
            row.error_message = f"{type(exc).__name__}: {exc}"
            row.duration_ms = int((time.monotonic() - started) * 1000)
            row.completed_at = _utcnow()
            audit_db.commit()
            audit_db.close()
            _log.error(
                "llm_call_timeout",
                purpose=purpose,
                provider=self.provider.name,
                timeout_seconds=exc.timeout_seconds,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - boundary; log + record + re-raise
            row.status = LLMCallStatus.FAILED
            row.error_message = f"{type(exc).__name__}: {exc}"
            row.duration_ms = int((time.monotonic() - started) * 1000)
            row.completed_at = _utcnow()
            audit_db.commit()
            audit_db.close()
            _log.error(
                "llm_call_failed",
                purpose=purpose,
                provider=self.provider.name,
                error=row.error_message,
            )
            raise

        row.status = LLMCallStatus.COMPLETED
        row.input_tokens = response.input_tokens
        row.output_tokens = response.output_tokens
        row.duration_ms = int((time.monotonic() - started) * 1000)
        row.completed_at = _utcnow()
        audit_db.commit()
        audit_db.close()

        _log.info(
            "llm_call_completed",
            purpose=purpose,
            provider=self.provider.name,
            model=model or self.provider.model,
            mode=call_mode.value,
            duration_ms=row.duration_ms,
            redacted=removed_counts,
        )
        return response, row

    def _complete_with_deadline(
        self,
        prompt: str,
        send_payload: dict[str, Any],
        *,
        model: str | None,
        max_tokens: int | None,
    ) -> LLMResponse:
        """Run provider.complete under a whole-call deadline (E-1).

        The provider call runs in a daemon worker thread joined with the
        configured timeout. On expiry the worker is abandoned (it can only ever
        return an LLMResponse to this method; it never touches request state), and
        LLMTimeoutError is raised so the caller applies nothing.
        """
        timeout = self._settings.shield_llm_timeout_seconds
        if not timeout or timeout <= 0:
            return self.provider.complete(prompt, send_payload, model=model, max_tokens=max_tokens)

        box: dict[str, Any] = {}

        def _worker() -> None:
            try:
                box["response"] = self.provider.complete(
                    prompt, send_payload, model=model, max_tokens=max_tokens
                )
            except BaseException as exc:  # noqa: BLE001 - relayed to the caller thread
                box["error"] = exc

        worker = threading.Thread(target=_worker, name="llm-complete", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise LLMTimeoutError(timeout)
        if "error" in box:
            raise box["error"]
        return box["response"]


LLMMode = Literal["fixture", "live"]
