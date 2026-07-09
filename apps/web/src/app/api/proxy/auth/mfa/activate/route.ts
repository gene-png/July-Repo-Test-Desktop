import type { NextResponse } from "next/server";

import { authedProxy } from "../../_proxy";

export async function POST(request: Request): Promise<NextResponse> {
  return authedProxy(request, "/auth/mfa/activate");
}
