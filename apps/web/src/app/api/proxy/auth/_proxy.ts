/**
 * Shared proxy helpers for the authenticated + public auth routes
 * (MFA enroll/activate/disable, resend + confirm email verification).
 *
 * Mirrors apps/web/src/app/api/proxy/csf/_proxy.ts: thin pass-throughs to the
 * FastAPI backend that keep the API host and bearer token off the wire to the
 * browser.
 */

import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

function relay(err: unknown): NextResponse {
  if (err instanceof ApiError) {
    return NextResponse.json(err.payload ?? { error: { code: err.status } }, {
      status: err.status,
    });
  }
  return NextResponse.json(
    { error: { message: "Upstream auth call failed." } },
    { status: 502 },
  );
}

/** Authenticated pass-through: attaches the session bearer. */
export async function authedProxy<T = unknown>(
  request: Request,
  upstream: string,
): Promise<NextResponse> {
  const session = await getServerSession(authOptions);
  const token = session?.accessToken;
  if (!token) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = undefined;
  }
  try {
    const result = await apiFetch<T>(upstream, {
      method: "POST",
      bearer: token,
      body: body as Record<string, unknown> | undefined,
    });
    return NextResponse.json(result ?? {});
  } catch (err) {
    return relay(err);
  }
}

/** Public pass-through (no session): e.g. confirming an email token. */
export async function publicProxy<T = unknown>(
  request: Request,
  upstream: string,
): Promise<NextResponse> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    body = undefined;
  }
  try {
    const result = await apiFetch<T>(upstream, {
      method: "POST",
      body: body as Record<string, unknown> | undefined,
    });
    return NextResponse.json(result ?? {});
  } catch (err) {
    return relay(err);
  }
}
