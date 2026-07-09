"use client";
import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import * as React from "react";

import { MFA_INVALID_CODE_ERROR, MFA_REQUIRED_ERROR } from "@/lib/auth/mfa";

import type { JSX } from "react";

export function SignInForm(): JSX.Element {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  // D-4: sign-up redirects here with ?registered=1 so we can confirm the
  // account was created before the user re-enters their credentials.
  const justRegistered = searchParams.get("registered") === "1";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [totp, setTotp] = React.useState("");
  // Once the backend signals a TOTP factor, we swap to the code step: the
  // email/password are locked in and only the 6-digit code is requested.
  const [mfaStep, setMfaStep] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>): Promise<void> {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const result = await signIn("credentials", {
        email,
        password,
        totp: mfaStep ? totp : "",
        redirect: false,
      });
      if (result?.error === MFA_REQUIRED_ERROR) {
        // Password accepted; ask for the authenticator code.
        setMfaStep(true);
        setError(null);
        setPending(false);
        return;
      }
      if (result?.error === MFA_INVALID_CODE_ERROR) {
        setError("That code didn't match. Try again.");
        setPending(false);
        return;
      }
      if (!result || result.error) {
        setError(
          mfaStep
            ? "That code didn't match. Try again."
            : "Invalid email or password.",
        );
        setPending(false);
        return;
      }
      // Full-page navigation (not router.replace) so the server re-renders the
      // header/nav with the new session cookie - a soft nav can serve the
      // cached logged-out tree and leave the nav stale until a manual refresh.
      window.location.assign(callbackUrl);
    } catch {
      setError("Something went wrong. Try again.");
      setPending(false);
    }
  }

  return (
    <form className="flex flex-col gap-5" onSubmit={onSubmit} noValidate>
      {justRegistered ? (
        <div
          role="status"
          className="rounded-md border border-status-success-border bg-status-success-bg px-3 py-2 text-sm text-status-success-fg"
        >
          Account created. Sign in to continue.
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="email" className="text-sm font-medium text-ink-primary">
          Email
        </label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          required
          disabled={mfaStep}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm text-ink-primary placeholder:text-ink-tertiary focus:border-border-focus focus:outline-none disabled:opacity-60"
          placeholder="you@example.gov"
        />
      </div>
      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="password"
          className="text-sm font-medium text-ink-primary"
        >
          Password
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          minLength={1}
          disabled={mfaStep}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm text-ink-primary focus:border-border-focus focus:outline-none disabled:opacity-60"
        />
      </div>
      {mfaStep ? (
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="totp"
            className="text-sm font-medium text-ink-primary"
          >
            Authentication code
          </label>
          <input
            id="totp"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]*"
            maxLength={6}
            required
            autoFocus
            value={totp}
            onChange={(e) => setTotp(e.target.value)}
            className="rounded-md border border-border bg-surface-card px-3 py-2 text-sm tracking-widest text-ink-primary focus:border-border-focus focus:outline-none"
            placeholder="123456"
          />
          <p className="text-xs text-ink-tertiary">
            Enter the 6-digit code from your authenticator app.
          </p>
        </div>
      ) : null}
      {error ? (
        <div
          role="alert"
          className="rounded-md border border-status-danger-border bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg"
        >
          {error}
        </div>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-brand-500 px-4 py-2.5 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {pending ? "Signing in…" : mfaStep ? "Verify code" : "Sign in"}
      </button>
    </form>
  );
}
