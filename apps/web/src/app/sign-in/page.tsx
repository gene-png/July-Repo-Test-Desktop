import type { Metadata } from "next";
import Link from "next/link";
import { Suspense, type JSX } from "react";

import { SignInForm } from "@/components/auth/SignInForm";
import { PublicHeader } from "@/components/site/PublicHeader";

export const metadata: Metadata = {
  title: "Sign in",
};

export default function SignInPage(): JSX.Element {
  return (
    <>
      <PublicHeader />
      <main className="mx-auto flex w-full max-w-md flex-col gap-6 px-6 py-16">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold text-ink-primary">Sign in</h1>
          <p className="text-sm text-ink-secondary">
            Use the email and password you registered with. MFA enrollment is on
            the v1.x roadmap.
          </p>
        </header>
        <Suspense
          fallback={
            <div aria-busy="true" className="text-sm text-ink-tertiary">
              Loading…
            </div>
          }
        >
          <SignInForm />
        </Suspense>
        <p className="text-sm text-ink-secondary">
          New here?{" "}
          <Link
            href="/sign-up"
            className="font-medium text-brand-500 hover:text-brand-600"
          >
            Create an account
          </Link>
        </p>
      </main>
    </>
  );
}
