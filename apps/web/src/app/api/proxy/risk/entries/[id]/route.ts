import { proxyJson } from "../../_proxy";

export async function PATCH(
  request: Request,
  { params }: { params: { id: string } },
) {
  let body: unknown = undefined;
  try {
    body = await request.json();
  } catch {
    body = {};
  }
  return proxyJson(`/risk/entries/${params.id}`, { method: "PATCH", body });
}

export async function DELETE(
  _request: Request,
  { params }: { params: { id: string } },
) {
  return proxyJson(`/risk/entries/${params.id}`, { method: "DELETE" });
}
