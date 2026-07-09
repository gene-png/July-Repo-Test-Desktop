"use client";

import * as React from "react";
import Link from "next/link";

import {
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  StatusPill,
} from "@shield/design-system";

import { MessageThread } from "@/components/messages/MessageThread";
import { fetchAssessments } from "@/lib/intake/client";
import { SERVICE_LABELS, type AssessmentResponse } from "@/lib/intake/types";

function fmtTime(value: string | null): string {
  if (!value) return "";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusTone(s: string): "info" | "warning" | "success" | "neutral" {
  if (s === "released" || s === "approved") return "success";
  if (s === "submitted") return "warning";
  if (s === "draft" || s === "in_progress") return "info";
  return "neutral";
}

/** Ordered lifecycle steps we can surface from the assessments list. */
function timeline(e: AssessmentResponse): { label: string; at: string }[] {
  const steps: { label: string; at: string }[] = [];
  if (e.created_at) steps.push({ label: "Created", at: fmtTime(e.created_at) });
  return steps;
}

export function ClientServiceDetail({
  serviceId,
}: {
  serviceId: string;
}): JSX.Element {
  const [service, setService] = React.useState<AssessmentResponse | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let active = true;
    fetchAssessments()
      .then((rows) => {
        if (!active) return;
        setService(rows.find((r) => r.service_id === serviceId) ?? null);
      })
      .catch((err) => {
        if (active)
          setError(err instanceof Error ? err.message : "Failed to load.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [serviceId]);

  if (loading) {
    return (
      <p className="text-sm text-ink-tertiary" aria-live="polite">
        Loading…
      </p>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Couldn&apos;t load this service</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
        </CardBody>
      </Card>
    );
  }

  if (!service) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Service not found</CardTitle>
        </CardHeader>
        <CardBody className="flex flex-col items-start gap-3">
          <p className="text-sm text-ink-secondary">
            We couldn&apos;t find this service on your account.
          </p>
          <Link
            href="/assessments"
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600"
          >
            Back to My Assessments
          </Link>
        </CardBody>
      </Card>
    );
  }

  const status = service.assessment_status ?? service.status;
  const steps = timeline(service);

  return (
    <div className="flex flex-col gap-6">
      <header className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-500">
          {SERVICE_LABELS[service.service_type]}
        </p>
        <h1 className="text-3xl font-semibold text-ink-primary">
          {service.title}
        </h1>
      </header>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle>Status</CardTitle>
            <StatusPill tone={statusTone(status)} withDot>
              {status}
            </StatusPill>
          </div>
        </CardHeader>
        <CardBody>
          <ol className="flex flex-col gap-3">
            {steps.map((s) => (
              <li key={s.label} className="flex items-baseline gap-3 text-sm">
                <span
                  aria-hidden
                  className="mt-1 h-2 w-2 shrink-0 rounded-full bg-brand-500"
                />
                <span className="font-medium text-ink-primary">{s.label}</span>
                <span className="text-ink-tertiary">{s.at}</span>
              </li>
            ))}
          </ol>
          <p className="mt-4 text-sm text-ink-secondary">
            Your SHIELD analyst is handling this engagement. Use the thread
            below to share information or ask a question.
          </p>
        </CardBody>
      </Card>

      <MessageThread serviceId={serviceId} />
    </div>
  );
}
