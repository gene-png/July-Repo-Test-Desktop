import type { Metadata } from "next";

import { AuditLogView } from "@/components/admin/AuditLogView";
import { Breadcrumbs } from "@/components/site/Breadcrumbs";

import type { JSX } from "react";

export const metadata: Metadata = { title: "Audit log" };

export default function AuditLogPage(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs items={[{ label: "Audit log" }]} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-primary">Audit log</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          The append-only record of every state-changing action across the
          platform. Filter by client, action, or date, and export the current
          view as CSV.
        </p>
      </div>
      <AuditLogView />
    </div>
  );
}
