"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";

import type { JSX } from "react";

type State = "loading" | "ok" | "error";

export function VerifyEmailClient(): JSX.Element {
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = React.useState<State>("loading");
  // Guard against the effect running twice (React strict mode / remount):
  // the token is single-use, so a second POST would 400 and flip us to error.
  const ran = React.useRef(false);

  React.useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    if (!token) {
      setState("error");
      return;
    }
    fetch("/api/proxy/auth/verify-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((res) => setState(res.ok ? "ok" : "error"))
      .catch(() => setState("error"));
  }, [token]);

  if (state === "loading") {
    return (
      <p aria-busy="true" className="text-sm text-ink-tertiary">
        Verifying your email…
      </p>
    );
  }

  if (state === "ok") {
    return (
      <div className="flex flex-col gap-4">
        <div
          role="status"
          className="rounded-md border border-status-success-border bg-status-success-bg px-3 py-2 text-sm text-status-success-fg"
        >
          Your email address has been verified.
        </div>
        <Link
          href="/sign-in"
          className="font-medium text-brand-500 hover:text-brand-600"
        >
          Continue to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        role="alert"
        className="rounded-md border border-status-danger-border bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg"
      >
        This verification link is invalid or has expired.
      </div>
      <p className="text-sm text-ink-secondary">
        Sign in and request a new verification email from your account page.
      </p>
      <Link
        href="/sign-in"
        className="font-medium text-brand-500 hover:text-brand-600"
      >
        Go to sign in
      </Link>
    </div>
  );
}
