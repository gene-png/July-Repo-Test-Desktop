"use client";
import * as React from "react";

import { cn } from "@shield/design-system";

import type { JSX } from "react";

export type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; at: number }
  | { kind: "error"; message: string };

export interface SaveStatusProps {
  state: SaveState;
  className?: string;
}

const ONE_MINUTE = 60_000;

export function SaveStatus({
  state,
  className,
}: SaveStatusProps): JSX.Element | null {
  const [, force] = React.useReducer((x: number) => x + 1, 0);

  // Re-render once a second so "saved 2 seconds ago" stays accurate.
  React.useEffect(() => {
    if (state.kind !== "saved") return;
    const id = window.setInterval(() => force(), 1000);
    return () => window.clearInterval(id);
  }, [state.kind]);

  if (state.kind === "idle") return null;

  if (state.kind === "saving") {
    return (
      <span
        aria-live="polite"
        className={cn("text-sm text-ink-tertiary", className)}
      >
        Saving…
      </span>
    );
  }

  if (state.kind === "error") {
    return (
      <span
        role="alert"
        className={cn("text-sm text-status-danger-fg", className)}
      >
        Couldn&apos;t save: {state.message}
      </span>
    );
  }

  const elapsed = Date.now() - state.at;
  const label =
    elapsed < 5_000
      ? "Saved"
      : elapsed < ONE_MINUTE
        ? `Saved ${Math.round(elapsed / 1000)} seconds ago`
        : `Saved ${Math.round(elapsed / ONE_MINUTE)} minutes ago`;
  return (
    <span
      aria-live="polite"
      className={cn("text-sm text-status-success-fg", className)}
    >
      {label}
    </span>
  );
}
