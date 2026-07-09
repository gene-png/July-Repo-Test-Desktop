import type { Metadata } from "next";

import { EngagementsView } from "@/components/admin/EngagementsView";
import { Breadcrumbs } from "@/components/site/Breadcrumbs";

import type { JSX } from "react";

export const metadata: Metadata = { title: "Engagements" };

export default function EngagementsPage(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs items={[{ label: "Engagements" }]} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-primary">Engagements</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Every service / engagement across all clients. Archive an engagement
          to remove it from active lists; its data is retained.
        </p>
      </div>
      <EngagementsView />
    </div>
  );
}
