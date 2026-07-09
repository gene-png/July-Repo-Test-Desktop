"use client";

import type {
  AssessmentCreateRequest,
  AssessmentResponse,
  IntakePatchRequest,
  IntakeStateResponse,
  IntakeSubmitRequest,
} from "./types";

/**
 * Client-side wrappers that call the same-origin proxy routes. The proxy
 * (apps/web/src/app/api/proxy/intake/...) attaches the user's bearer
 * token server-side, keeping the API host name and the access token off
 * the wire to the browser.
 */

/** Friendly, user-facing copy per status when the payload has no typed message. */
function genericProxyMessage(status: number): string {
  if (status === 401) return "Please sign in and try again.";
  if (status === 403) return "You don't have access to do that.";
  if (status === 404) return "We couldn't find that.";
  if (status === 409)
    return "That conflicts with the current state — reload and try again.";
  if (status === 422)
    return "Some details need attention before we can continue.";
  if (status >= 500)
    return "Something went wrong on our end. Please try again.";
  return `Request failed (${status}).`;
}

/** Prefer the API's typed message ({error.message} or {detail}) over a generic. */
function proxyMessage(status: number, payload: unknown): string {
  const p = payload as
    | { error?: { message?: string }; detail?: unknown }
    | undefined;
  const typed =
    p?.error?.message ?? (typeof p?.detail === "string" ? p.detail : undefined);
  return typed && typed.trim().length > 0 ? typed : genericProxyMessage(status);
}

class ProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    // Surface the API's typed detail (e.g. the incomplete-intake message)
    // instead of the raw "Intake proxy <status>" placeholder.
    super(proxyMessage(status, payload));
  }
}

/** True when the failure is the "complete intake first" guard from the API. */
export function isIncompleteIntakeError(err: unknown): boolean {
  if (!(err instanceof ProxyError)) return false;
  // The API wraps HTTPException detail as {error: {message}}; some callers
  // may see a raw {detail} instead. Accept either, like proxyMessage does.
  const p = err.payload as
    | { detail?: unknown; error?: { message?: unknown } }
    | undefined;
  const text =
    (typeof p?.error?.message === "string" ? p.error.message : "") ||
    (typeof p?.detail === "string" ? p.detail : "");
  return err.status === 422 && /intake/i.test(text);
}

export async function fetchIntake(): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake", { cache: "no-store" });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function patchIntake(
  body: IntakePatchRequest,
): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function submitIntake(
  body: IntakeSubmitRequest,
): Promise<IntakeStateResponse> {
  const res = await fetch("/api/proxy/intake/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as IntakeStateResponse;
}

export async function fetchAssessments(): Promise<AssessmentResponse[]> {
  const res = await fetch("/api/proxy/intake/assessments", {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as AssessmentResponse[];
}

export async function createAssessment(
  body: AssessmentCreateRequest,
): Promise<AssessmentResponse> {
  const res = await fetch("/api/proxy/intake/assessments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new ProxyError(res.status, await safeJson(res));
  }
  return (await res.json()) as AssessmentResponse;
}

async function safeJson(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return await res.text();
  }
}

export { ProxyError };
