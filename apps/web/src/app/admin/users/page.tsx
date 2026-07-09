import type { Metadata } from "next";

import { UsersView } from "@/components/admin/UsersView";
import { Breadcrumbs } from "@/components/site/Breadcrumbs";

import type { JSX } from "react";

export const metadata: Metadata = { title: "Users" };

export default function UsersPage(): JSX.Element {
  return (
    <div className="flex flex-col gap-6">
      <Breadcrumbs items={[{ label: "Users" }]} />
      <div>
        <h1 className="text-2xl font-semibold text-ink-primary">Users</h1>
        <p className="mt-1 text-sm text-ink-secondary">
          Every account on the platform. Create admin or client users, and
          deactivate accounts. Self-registration only ever creates client users.
        </p>
      </div>
      <UsersView />
    </div>
  );
}
