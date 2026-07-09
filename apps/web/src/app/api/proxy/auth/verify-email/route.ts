import type { NextResponse } from "next/server";

import { publicProxy } from "../_proxy";

export async function POST(request: Request): Promise<NextResponse> {
  return publicProxy(request, "/auth/verify-email");
}
