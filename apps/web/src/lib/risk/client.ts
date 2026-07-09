"use client";

import type {
  RiskEntry,
  RiskEntryPatch,
  RiskGate,
  RiskRegister,
} from "./types";

export class RiskProxyError extends Error {
  constructor(
    public readonly status: number,
    public readonly payload: unknown,
  ) {
    super(`Risk proxy ${status}`);
  }
}

async function jsonRequest<T>(
  url: string,
  init: {
    method?: "GET" | "POST" | "PATCH" | "DELETE";
    body?: unknown;
  } = {},
): Promise<T> {
  const res = await fetch(url, {
    method: init.method ?? "GET",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init.body !== undefined
        ? { "Content-Type": "application/json" }
        : {}),
    },
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 204) {
    return undefined as T;
  }
  if (!res.ok) {
    let payload: unknown;
    try {
      payload = await res.json();
    } catch {
      payload = await res.text();
    }
    throw new RiskProxyError(res.status, payload);
  }
  return (await res.json()) as T;
}

export async function getActiveClientId(): Promise<string | null> {
  const { active } = await jsonRequest<{ active: string | null }>(
    "/api/active-client",
  );
  return active;
}

export async function getClientName(cid: string): Promise<string> {
  try {
    const clients = await jsonRequest<{ id: string; legal_name: string }[]>(
      "/api/proxy/admin/clients",
    );
    return clients.find((c) => c.id === cid)?.legal_name ?? "Client";
  } catch {
    return "Client";
  }
}

export async function fetchRiskGate(cid: string): Promise<RiskGate> {
  return jsonRequest<RiskGate>(`/api/proxy/risk/clients/${cid}/gate`);
}

export async function fetchRiskRegisterLatest(
  cid: string,
): Promise<RiskRegister | null> {
  try {
    return await jsonRequest<RiskRegister>(
      `/api/proxy/risk/clients/${cid}/register/latest`,
    );
  } catch (err) {
    if (err instanceof RiskProxyError && err.status === 404) return null;
    throw err;
  }
}

export async function generateRiskRegister(cid: string): Promise<RiskRegister> {
  return jsonRequest<RiskRegister>(
    `/api/proxy/risk/clients/${cid}/register/generate`,
    { method: "POST" },
  );
}

export async function exportRiskRegister(cid: string): Promise<RiskRegister> {
  return jsonRequest<RiskRegister>(
    `/api/proxy/risk/clients/${cid}/register/export`,
    { method: "POST" },
  );
}

export async function approveRiskRegister(cid: string): Promise<RiskRegister> {
  return jsonRequest<RiskRegister>(
    `/api/proxy/risk/clients/${cid}/register/approve`,
    { method: "POST" },
  );
}

export async function patchRiskEntry(
  entryId: string,
  patch: RiskEntryPatch,
): Promise<RiskEntry> {
  return jsonRequest<RiskEntry>(`/api/proxy/risk/entries/${entryId}`, {
    method: "PATCH",
    body: patch,
  });
}

export async function deleteRiskEntry(entryId: string): Promise<void> {
  await jsonRequest<void>(`/api/proxy/risk/entries/${entryId}`, {
    method: "DELETE",
  });
}

export function describeRiskError(err: unknown): string {
  if (err instanceof RiskProxyError) {
    const payload = err.payload as
      | { error?: { message?: string }; detail?: string }
      | undefined;
    return (
      payload?.error?.message ??
      payload?.detail ??
      `Request failed (${err.status}).`
    );
  }
  return err instanceof Error ? err.message : "Request failed.";
}
