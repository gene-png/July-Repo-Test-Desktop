import { proxyJson } from "../../_proxy";

export async function PATCH(
  request: Request,
  props: { params: Promise<{ id: string }> },
) {
  const params = await props.params;
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
  props: { params: Promise<{ id: string }> },
) {
  const params = await props.params;
  return proxyJson(`/risk/entries/${params.id}`, { method: "DELETE" });
}
