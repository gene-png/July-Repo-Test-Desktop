import { proxyJson } from "../../../../_proxy";

export async function POST(
  _request: Request,
  { params }: { params: { cid: string } },
) {
  return proxyJson(`/risk/clients/${params.cid}/register/approve`, {
    method: "POST",
  });
}
