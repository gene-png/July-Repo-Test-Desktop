/**
 * Inline HTML passthrough for the artifact view endpoint. Mirrors the
 * download proxy but hits /artifacts/{id}/view, which the backend serves
 * with Content-Disposition: inline + a strict CSP, so the deliverable
 * dashboard renders in a new browser tab instead of downloading.
 */

import { cookies } from "next/headers";
import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { ACTIVE_CLIENT_COOKIE } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

const BASE_URL = process.env.API_BASE_URL ?? "http://api:8000";

export async function GET(
  _request: Request,
  { params }: { params: { id: string } },
): Promise<Response> {
  const session = await getServerSession(authOptions);
  const token = session?.accessToken;
  if (!token) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }
  const reqHeaders: Record<string, string> = {
    Authorization: `Bearer ${token}`,
  };
  const activeClient = cookies().get(ACTIVE_CLIENT_COOKIE)?.value;
  if (activeClient) {
    reqHeaders["X-Client-Id"] = activeClient;
  }
  const upstream = await fetch(`${BASE_URL}/artifacts/${params.id}/view`, {
    headers: reqHeaders,
    cache: "no-store",
  });
  if (!upstream.ok) {
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("Content-Type") ?? "application/json",
      },
    });
  }
  const headers = new Headers();
  const ct = upstream.headers.get("Content-Type");
  if (ct) headers.set("Content-Type", ct);
  const cd = upstream.headers.get("Content-Disposition");
  if (cd) headers.set("Content-Disposition", cd);
  const csp = upstream.headers.get("Content-Security-Policy");
  if (csp) headers.set("Content-Security-Policy", csp);
  headers.set("X-Content-Type-Options", "nosniff");
  return new Response(upstream.body, { status: 200, headers });
}
