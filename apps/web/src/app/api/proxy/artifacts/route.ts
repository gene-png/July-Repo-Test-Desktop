/**
 * Multipart proxy for /artifacts. The Next.js route reads the inbound
 * FormData, attaches the session's access token, and forwards to the
 * FastAPI upload endpoint. The browser never sees the API host name or
 * the access token.
 */

import { cookies } from "next/headers";
import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { ACTIVE_CLIENT_COOKIE } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

const BASE_URL = process.env.API_BASE_URL ?? "http://api:8000";

// Mirror the API's per-file cap (apps/api/app/routes/artifacts.py
// MAX_UPLOAD_BYTES). Reject an oversized body here before buffering the
// multipart form, so a huge upload fails fast at the edge.
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024; // 50 MB

async function bearerOrUnauthorized(): Promise<string | NextResponse> {
  const session = await getServerSession(authOptions);
  const token = session?.accessToken;
  if (!token) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }
  return token;
}

/**
 * Bearer + tenant headers for the upstream call. This route hand-rolls the
 * proxy (multipart can't go through `lib/api.ts`), so we forward the active
 * client cookie as X-Client-Id ourselves - otherwise admin/reviewer uploads
 * hit the backend's `current_client` guard and 400. Don't set Content-Type:
 * `fetch` derives the multipart boundary from the FormData body.
 */
function upstreamHeaders(bearer: string): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${bearer}`,
  };
  const activeClient = cookies().get(ACTIVE_CLIENT_COOKIE)?.value;
  if (activeClient) {
    headers["X-Client-Id"] = activeClient;
  }
  return headers;
}

export async function POST(request: Request): Promise<NextResponse> {
  const bearer = await bearerOrUnauthorized();
  if (bearer instanceof NextResponse) return bearer;

  // Content-Length pre-check (C-6): bail before reading the body when the
  // declared size already exceeds the cap.
  const contentLength = request.headers.get("content-length");
  if (contentLength && Number(contentLength) > MAX_UPLOAD_BYTES) {
    return NextResponse.json(
      {
        error: {
          code: 413,
          message: `File exceeds the ${MAX_UPLOAD_BYTES} byte upload limit.`,
        },
      },
      { status: 413 },
    );
  }

  // Forward the FormData payload as-is so multipart boundaries and the
  // raw file bytes are preserved.
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json(
      { error: { code: 400, message: "Multipart body required." } },
      { status: 400 },
    );
  }

  const upstream = await fetch(`${BASE_URL}/artifacts`, {
    method: "POST",
    headers: upstreamHeaders(bearer),
    body: form,
  });
  const body = await upstream.text();
  try {
    return new NextResponse(body, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch {
    return NextResponse.json(
      { error: { message: "Upstream upload failed." } },
      { status: 502 },
    );
  }
}

export async function GET(): Promise<NextResponse> {
  const bearer = await bearerOrUnauthorized();
  if (bearer instanceof NextResponse) return bearer;
  const upstream = await fetch(`${BASE_URL}/artifacts`, {
    headers: upstreamHeaders(bearer),
    cache: "no-store",
  });
  const body = await upstream.text();
  return new NextResponse(body, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("Content-Type") ?? "application/json",
    },
  });
}
