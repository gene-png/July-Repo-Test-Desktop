"use client";

import Link from "next/link";
import * as React from "react";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusPill,
} from "@shield/design-system";

import { listClients, listServices } from "@/lib/admin/client";
import { workspaceHref } from "@/lib/admin/types";
import type { AdminServiceRow } from "@/lib/admin/types";
import { SERVICE_LABELS } from "@/lib/intake/types";

/** Statuses that count as "active work" — a live engagement being worked. */
const ACTIVE_STATUSES = new Set(["in_progress", "review"]);

export function ActiveWorkView(): JSX.Element {
  const [services, setServices] = React.useState<AdminServiceRow[] | null>(
    null,
  );
  const [clientNames, setClientNames] = React.useState<Record<string, string>>(
    {},
  );
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [rows, clients] = await Promise.all([
          listServices(false),
          listClients().catch(() => []),
        ]);
        if (!active) return;
        setServices(rows);
        setClientNames(
          Object.fromEntries(clients.map((c) => [c.id, c.legal_name])),
        );
      } catch (err) {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Failed to load active work.",
          );
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const activeRows = (services ?? []).filter((s) =>
    ACTIVE_STATUSES.has(s.status),
  );

  return (
    <div className="flex flex-col gap-4">
      {error ? (
        <p className="text-sm text-status-danger-fg" role="alert">
          {error}
        </p>
      ) : null}

      {services === null ? (
        <p className="text-sm text-ink-tertiary">Loading active work…</p>
      ) : activeRows.length === 0 ? (
        <EmptyState
          title="No active work"
          description="Assessments in progress across all clients appear here once a client starts a self-assessment or you publish a request for processing."
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>In progress ({activeRows.length})</CardTitle>
          </CardHeader>
          <CardBody className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-border-subtle text-xs uppercase tracking-wider text-ink-tertiary">
                  <th className="py-2 pr-4 font-medium">Engagement</th>
                  <th className="py-2 pr-4 font-medium">Client</th>
                  <th className="py-2 pr-4 font-medium">Type</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 font-medium">Started</th>
                  <th className="py-2 font-medium">Workspace</th>
                </tr>
              </thead>
              <tbody>
                {activeRows.map((s) => {
                  const href = workspaceHref(s.kind, s.id);
                  return (
                    <tr
                      key={s.id}
                      className="border-b border-border-subtle last:border-b-0"
                    >
                      <td className="py-2 pr-4 font-medium text-ink-primary">
                        {s.title}
                      </td>
                      <td className="py-2 pr-4 text-ink-secondary">
                        {clientNames[s.client_id] ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-ink-secondary">
                        {SERVICE_LABELS[s.kind] ?? s.kind}
                      </td>
                      <td className="py-2 pr-4">
                        <StatusPill
                          tone={s.status === "review" ? "warning" : "info"}
                          withDot
                        >
                          {s.status === "review" ? "In review" : "In progress"}
                        </StatusPill>
                      </td>
                      <td className="py-2 pr-4 text-ink-secondary">
                        {new Date(s.created_at).toLocaleDateString()}
                      </td>
                      <td className="py-2">
                        {href ? (
                          <Link
                            href={href}
                            className="font-semibold text-brand-500 hover:text-brand-600"
                          >
                            Open →
                          </Link>
                        ) : (
                          <span className="text-xs text-ink-tertiary">
                            No workspace
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
