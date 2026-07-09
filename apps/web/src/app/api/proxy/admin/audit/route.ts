/**
 * Audit-log passthrough (H-7). Forwards the caller's filters/pagination to the
 * FastAPI /admin/audit endpoint with the session bearer attached, and streams
 * the response straight back — JSON for the table, text/csv for the download
 * link (?format=csv). Admin-gated upstream; not tenant-scoped.
 */

import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { authOptions } from "@/lib/auth/options";

const BASE_URL = process.env.API_BASE_URL ?? "http://api:8000";

export async function GET(request: Request): Promise<Response> {
  const session = await getServerSession(authOptions);
  const token = session?.accessToken;
  if (!token) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }

  const search = new URL(request.url).search;
  const upstream = await fetch(`${BASE_URL}/admin/audit${search}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });

  const headers = new Headers();
  const ct = upstream.headers.get("Content-Type");
  if (ct) headers.set("Content-Type", ct);
  const cd = upstream.headers.get("Content-Disposition");
  if (cd) headers.set("Content-Disposition", cd);
  return new Response(upstream.body, { status: upstream.status, headers });
}
