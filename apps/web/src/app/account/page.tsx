import type { Metadata } from "next";
import { getServerSession } from "next-auth";

import { Card, CardBody, CardHeader, CardTitle } from "@shield/design-system";

import { AccountSecurity } from "@/components/auth/AccountSecurity";
import { PublicFooter } from "@/components/site/PublicFooter";
import { PublicHeader } from "@/components/site/PublicHeader";
import { SignOutButton } from "@/components/site/SignOutButton";
import { SkipToContent } from "@/components/site/SkipToContent";
import { apiFetch } from "@/lib/api";
import { authOptions } from "@/lib/auth/options";

import type { JSX } from "react";

export const metadata: Metadata = { title: "Account" };

interface MeResponse {
  mfa_enrolled: boolean;
  email_verified_at: string | null;
}

function Row({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-subtle py-2 last:border-0">
      <span className="text-sm font-medium text-ink-secondary">{label}</span>
      <span className="text-sm text-ink-primary">{value}</span>
    </div>
  );
}

export default async function AccountPage(): Promise<JSX.Element> {
  const session = await getServerSession(authOptions);
  const role = session?.role ?? "—";

  let me: MeResponse | null = null;
  if (session?.accessToken) {
    try {
      me = await apiFetch<MeResponse>("/auth/me", {
        bearer: session.accessToken,
      });
    } catch {
      me = null;
    }
  }

  return (
    <>
      <SkipToContent />
      <PublicHeader />
      <main
        id="main-content"
        className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-10"
      >
        <div>
          <h1 className="text-2xl font-semibold text-ink-primary">Account</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Your SHIELD profile and session.
          </p>
        </div>
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
          </CardHeader>
          <CardBody className="flex flex-col gap-1">
            <Row label="Email" value={session?.user?.email ?? "—"} />
            <Row label="Role" value={role} />
            <div className="pt-4">
              <SignOutButton />
            </div>
          </CardBody>
        </Card>
        {me ? (
          <Card>
            <CardHeader>
              <CardTitle>Security</CardTitle>
            </CardHeader>
            <CardBody>
              <AccountSecurity
                initialMfaEnrolled={me.mfa_enrolled}
                initialEmailVerified={me.email_verified_at !== null}
              />
            </CardBody>
          </Card>
        ) : null}
      </main>
      <PublicFooter />
    </>
  );
}
