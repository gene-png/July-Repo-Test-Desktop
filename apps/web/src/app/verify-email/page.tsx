import type { Metadata } from "next";
import { Suspense, type JSX } from "react";

import { VerifyEmailClient } from "@/components/auth/VerifyEmailClient";
import { PublicHeader } from "@/components/site/PublicHeader";

export const metadata: Metadata = {
  title: "Verify email",
};

export default function VerifyEmailPage(): JSX.Element {
  return (
    <>
      <PublicHeader />
      <main className="mx-auto flex w-full max-w-md flex-col gap-6 px-6 py-16">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold text-ink-primary">
            Email verification
          </h1>
        </header>
        <Suspense
          fallback={
            <div aria-busy="true" className="text-sm text-ink-tertiary">
              Loading…
            </div>
          }
        >
          <VerifyEmailClient />
        </Suspense>
      </main>
    </>
  );
}
