"use client";

import * as React from "react";

import type { JSX } from "react";

interface EnrollData {
  secret: string;
  otpauth_uri: string;
}

const inputClass =
  "rounded-md border border-border bg-surface-card px-3 py-2 text-sm text-ink-primary focus:border-border-focus focus:outline-none";
const primaryBtn =
  "rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60";
const secondaryBtn =
  "rounded-md border border-border px-4 py-2 text-sm font-medium text-ink-primary hover:bg-surface-muted disabled:cursor-not-allowed disabled:opacity-60";

async function postJson(
  path: string,
  body?: Record<string, unknown>,
): Promise<{ ok: boolean; status: number; data: unknown }> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data: unknown = undefined;
  try {
    data = await res.json();
  } catch {
    data = undefined;
  }
  return { ok: res.ok, status: res.status, data };
}

function errMessage(data: unknown, fallback: string): string {
  const msg = (data as { error?: { message?: string } })?.error?.message;
  return msg ?? fallback;
}

export function AccountSecurity({
  initialMfaEnrolled,
  initialEmailVerified,
}: {
  initialMfaEnrolled: boolean;
  initialEmailVerified: boolean;
}): JSX.Element {
  const [mfaEnrolled, setMfaEnrolled] = React.useState(initialMfaEnrolled);
  const [enroll, setEnroll] = React.useState<EnrollData | null>(null);
  const [activateCode, setActivateCode] = React.useState("");
  const [disableCode, setDisableCode] = React.useState("");
  const [mfaMsg, setMfaMsg] = React.useState<string | null>(null);
  const [mfaErr, setMfaErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const [emailVerified] = React.useState(initialEmailVerified);
  const [resendMsg, setResendMsg] = React.useState<string | null>(null);

  async function startEnroll(): Promise<void> {
    setBusy(true);
    setMfaErr(null);
    setMfaMsg(null);
    const { ok, data } = await postJson("/api/proxy/auth/mfa/enroll");
    setBusy(false);
    if (!ok) {
      setMfaErr(errMessage(data, "Could not start enrollment."));
      return;
    }
    setEnroll(data as EnrollData);
  }

  async function activate(): Promise<void> {
    setBusy(true);
    setMfaErr(null);
    const { ok, data } = await postJson("/api/proxy/auth/mfa/activate", {
      code: activateCode.trim(),
    });
    setBusy(false);
    if (!ok) {
      setMfaErr(errMessage(data, "That code didn't match. Try again."));
      return;
    }
    setMfaEnrolled(true);
    setEnroll(null);
    setActivateCode("");
    setMfaMsg("Two-factor authentication is now on.");
  }

  async function disable(): Promise<void> {
    setBusy(true);
    setMfaErr(null);
    const { ok, data } = await postJson("/api/proxy/auth/mfa/disable", {
      code: disableCode.trim(),
    });
    setBusy(false);
    if (!ok) {
      setMfaErr(errMessage(data, "That code didn't match. Try again."));
      return;
    }
    setMfaEnrolled(false);
    setDisableCode("");
    setMfaMsg("Two-factor authentication has been turned off.");
  }

  async function resend(): Promise<void> {
    setBusy(true);
    setResendMsg(null);
    const { ok } = await postJson("/api/proxy/auth/resend-verification");
    setBusy(false);
    setResendMsg(
      ok
        ? "Verification email sent. Check your inbox."
        : "Could not send the verification email. Try again.",
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-ink-primary">
          Two-factor authentication (TOTP)
        </h2>
        {mfaEnrolled ? (
          <>
            <p className="text-sm text-ink-secondary">
              Two-factor authentication is <strong>on</strong>. You will be
              asked for a 6-digit code from your authenticator app at sign-in.
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex flex-col gap-1.5">
                <label
                  htmlFor="disable-code"
                  className="text-sm font-medium text-ink-primary"
                >
                  Current code to disable
                </label>
                <input
                  id="disable-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={disableCode}
                  onChange={(e) => setDisableCode(e.target.value)}
                  className={inputClass}
                  placeholder="123456"
                />
              </div>
              <button
                type="button"
                onClick={disable}
                disabled={busy || disableCode.trim().length < 6}
                className={secondaryBtn}
              >
                Disable
              </button>
            </div>
          </>
        ) : enroll ? (
          <>
            <p className="text-sm text-ink-secondary">
              Add this account to your authenticator app, then enter the current
              code to finish. Scan the URI as a QR code, or enter the secret
              manually.
            </p>
            <dl className="flex flex-col gap-2 rounded-md border border-border-subtle bg-surface-muted p-3 text-sm">
              <div className="flex flex-col gap-1">
                <dt className="font-medium text-ink-secondary">Secret</dt>
                <dd className="break-all font-mono text-ink-primary">
                  {enroll.secret}
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="font-medium text-ink-secondary">otpauth URI</dt>
                <dd className="break-all font-mono text-xs text-ink-primary">
                  {enroll.otpauth_uri}
                </dd>
              </div>
            </dl>
            <div className="flex flex-wrap items-end gap-2">
              <div className="flex flex-col gap-1.5">
                <label
                  htmlFor="activate-code"
                  className="text-sm font-medium text-ink-primary"
                >
                  Code from your app
                </label>
                <input
                  id="activate-code"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  value={activateCode}
                  onChange={(e) => setActivateCode(e.target.value)}
                  className={inputClass}
                  placeholder="123456"
                />
              </div>
              <button
                type="button"
                onClick={activate}
                disabled={busy || activateCode.trim().length < 6}
                className={primaryBtn}
              >
                Activate
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-secondary">
              Protect your account with a time-based one-time code from an
              authenticator app.
            </p>
            <div>
              <button
                type="button"
                onClick={startEnroll}
                disabled={busy}
                className={primaryBtn}
              >
                Enroll
              </button>
            </div>
          </>
        )}
        {mfaMsg ? (
          <p role="status" className="text-sm text-status-success-fg">
            {mfaMsg}
          </p>
        ) : null}
        {mfaErr ? (
          <p role="alert" className="text-sm text-status-danger-fg">
            {mfaErr}
          </p>
        ) : null}
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold text-ink-primary">
          Email verification
        </h2>
        {emailVerified ? (
          <p className="text-sm text-status-success-fg">
            Your email address is verified.
          </p>
        ) : (
          <>
            <p className="text-sm text-ink-secondary">
              Your email address is not verified yet.
            </p>
            <div>
              <button
                type="button"
                onClick={resend}
                disabled={busy}
                className={secondaryBtn}
              >
                Resend verification email
              </button>
            </div>
          </>
        )}
        {resendMsg ? (
          <p role="status" className="text-sm text-ink-secondary">
            {resendMsg}
          </p>
        ) : null}
      </section>
    </div>
  );
}
