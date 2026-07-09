"use client";

import * as React from "react";
import Link from "next/link";

import { Card, CardBody, EmptyState } from "@shield/design-system";

import { SERVICE_LABELS, type ServiceType } from "@/lib/intake/types";
import {
  describeMessagesError,
  fetchInbox,
  type InboxThread,
} from "@/lib/messages/client";

const SELF_ASSESSMENT_TYPES: ReadonlyArray<string> = [
  "nist_csf",
  "zero_trust_cisa",
  "zero_trust_dod",
];

/** Client-facing destination for a thread (never an admin workspace). */
function threadHref(thread: InboxThread): string {
  if (SELF_ASSESSMENT_TYPES.includes(thread.service_kind)) {
    return `/self-assessment/${thread.service_id}?type=${thread.service_kind}`;
  }
  return `/client-services/${thread.service_id}`;
}

function fmtTime(value: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function ThreadRow({ thread }: { thread: InboxThread }): JSX.Element {
  const kind = thread.service_kind as ServiceType;
  const label = SERVICE_LABELS[kind] ?? thread.service_kind;
  return (
    <Card>
      <CardBody>
        <Link href={threadHref(thread)} className="block hover:opacity-80">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-ink-primary">
                  {thread.service_title}
                </span>
                {thread.unread > 0 ? (
                  <span className="rounded-full bg-brand-500 px-2 py-0.5 text-xs font-semibold text-ink-on-accent">
                    {thread.unread} new
                  </span>
                ) : null}
              </div>
              <p className="truncate text-sm text-ink-secondary">
                {label} · {thread.last_preview ?? "No messages"}
              </p>
            </div>
            <span className="shrink-0 text-xs text-ink-tertiary">
              {fmtTime(thread.last_at)}
            </span>
          </div>
        </Link>
      </CardBody>
    </Card>
  );
}

export function ClientInbox(): JSX.Element {
  const [threads, setThreads] = React.useState<InboxThread[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    fetchInbox()
      .then((r) => {
        if (active) setThreads(r.threads);
      })
      .catch((err) => {
        if (active) setError(describeMessagesError(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) return <p className="text-sm text-ink-tertiary">Loading…</p>;
  if (error)
    return (
      <p className="text-sm text-status-danger-fg" role="alert">
        {error}
      </p>
    );
  if (threads.length === 0)
    return (
      <EmptyState
        title="No messages yet"
        description="Conversations appear here once an assessment has a thread. Start or open an assessment to begin one."
      />
    );

  return (
    <div className="flex flex-col gap-3">
      {threads.map((t) => (
        <ThreadRow key={t.service_id} thread={t} />
      ))}
    </div>
  );
}
