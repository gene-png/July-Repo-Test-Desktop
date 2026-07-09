/**
 * DELETE /api/proxy/admin/users/:id - deactivate a user account (admin).
 * Admin-only; cross-tenant by design.
 */

import { getServerSession } from "next-auth";
import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

export async function DELETE(
  _request: Request,
  props: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const params = await props.params;
  const session = await getServerSession(authOptions);
  const bearer = session?.accessToken;
  if (!bearer) {
    return NextResponse.json(
      { error: { code: 401, message: "Not signed in." } },
      { status: 401 },
    );
  }
  try {
    await apiFetch<unknown>(`/admin/users/${params.id}`, {
      method: "DELETE",
      bearer,
      clientId: "",
    });
    return new NextResponse(null, { status: 204 });
  } catch (err) {
    if (err instanceof ApiError) {
      return NextResponse.json(err.payload ?? { error: { code: err.status } }, {
        status: err.status,
      });
    }
    return NextResponse.json(
      { error: { message: "Upstream admin/users call failed." } },
      { status: 502 },
    );
  }
}
