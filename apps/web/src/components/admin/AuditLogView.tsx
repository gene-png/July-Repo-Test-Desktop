"use client";

import * as React from "react";

import { Card, CardBody, EmptyState } from "@shield/design-system";

import {
  auditCsvHref,
  fetchAuditLog,
  listClients,
  type ClientSummary,
} from "@/lib/admin/client";
import type { AdminAuditListResponse, AuditFilters } from "@/lib/admin/types";

const PAGE_SIZE = 50;

function fmtTime(value: string): string {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function AuditLogView(): JSX.Element {
  const [clients, setClients] = React.useState<ClientSummary[]>([]);
  const [clientId, setClientId] = React.useState("");
  const [action, setAction] = React.useState("");
  const [dateFrom, setDateFrom] = React.useState("");
  const [dateTo, setDateTo] = React.useState("");
  const [offset, setOffset] = React.useState(0);

  const [data, setData] = React.useState<AdminAuditListResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);

  // The filter set actually in effect (applied), separate from the draft inputs
  // above so typing doesn't refetch on every keystroke.
  const [applied, setApplied] = React.useState<AuditFilters>({});

  React.useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => setClients([]));
  }, []);

  const activeFilters = React.useMemo<AuditFilters>(
    () => ({ ...applied, limit: PAGE_SIZE, offset }),
    [applied, offset],
  );

  React.useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetchAuditLog(activeFilters)
      .then((d) => {
        if (alive) setData(d);
      })
      .catch((err) => {
        if (alive)
          setError(
            err instanceof Error ? err.message : "Failed to load audit log.",
          );
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [activeFilters]);

  function onApply(): void {
    setOffset(0);
    setApplied({
      client_id: clientId || undefined,
      action: action.trim() || undefined,
      // <input type="date"> yields YYYY-MM-DD; widen the upper bound to the end
      // of the chosen day so date_to is inclusive.
      date_from: dateFrom || undefined,
      date_to: dateTo ? `${dateTo}T23:59:59` : undefined,
    });
  }

  function onReset(): void {
    setClientId("");
    setAction("");
    setDateFrom("");
    setDateTo("");
    setOffset(0);
    setApplied({});
  }

  const total = data?.total ?? 0;
  const rows = data?.rows ?? [];
  const pageStart = total === 0 ? 0 : offset + 1;
  const pageEnd = offset + rows.length;
  const clientName = (id: string | null): string => {
    if (!id) return "—";
    return clients.find((c) => c.id === id)?.legal_name ?? id.slice(0, 8);
  };

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardBody className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Client</span>
              <select
                value={clientId}
                onChange={(e) => setClientId(e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              >
                <option value="">All clients</option>
                {clients.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.legal_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Action</span>
              <input
                type="text"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                placeholder="e.g. user.created"
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">From</span>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">To</span>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onApply}
              className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
            >
              Apply filters
            </button>
            <button
              type="button"
              onClick={onReset}
              className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted"
            >
              Reset
            </button>
            <a
              href={auditCsvHref(applied)}
              className="ml-auto rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted"
            >
              Download CSV
            </a>
          </div>
        </CardBody>
      </Card>

      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : null}

      {loading && data === null ? (
        <p className="text-sm text-ink-tertiary">Loading audit log…</p>
      ) : rows.length === 0 ? (
        <EmptyState
          title="No audit entries"
          description="No entries match the current filters."
        />
      ) : (
        <Card>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">When</th>
                  <th className="py-2 pr-4 font-medium">Action</th>
                  <th className="py-2 pr-4 font-medium">Target</th>
                  <th className="py-2 pr-4 font-medium">Client</th>
                  <th className="py-2 font-medium">Actor</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    className="border-b border-border-subtle last:border-b-0"
                  >
                    <td className="whitespace-nowrap py-2 pr-4 text-ink-secondary">
                      {fmtTime(r.at)}
                    </td>
                    <td className="py-2 pr-4 font-medium text-ink-primary">
                      {r.action}
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {r.target_type}
                      {r.target_id ? (
                        <span className="ml-1 text-ink-tertiary">
                          {r.target_id.slice(0, 8)}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pr-4 text-ink-secondary">
                      {clientName(r.client_id)}
                    </td>
                    <td className="py-2 text-ink-secondary">
                      {r.actor_user_id ? r.actor_user_id.slice(0, 8) : "system"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-ink-tertiary">
          {total === 0
            ? "0 entries"
            : `Showing ${pageStart}–${pageEnd} of ${total}`}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            disabled={offset === 0 || loading}
            className="rounded-md border border-border-default px-3 py-1.5 text-sm font-medium text-ink-primary hover:bg-surface-muted disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setOffset((o) => o + PAGE_SIZE)}
            disabled={pageEnd >= total || loading}
            className="rounded-md border border-border-default px-3 py-1.5 text-sm font-medium text-ink-primary hover:bg-surface-muted disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
