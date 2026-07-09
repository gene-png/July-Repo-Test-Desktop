"use client";
import * as React from "react";

import type { JSX } from "react";

const TIER_SHORT_LABELS = [
  { tier: 1, label: "Partial" },
  { tier: 2, label: "Risk Informed" },
  { tier: 3, label: "Repeatable" },
  { tier: 4, label: "Adaptive" },
] as const;

export interface TierPickerProps {
  value: number | null;
  onChange: (next: number | null) => void;
  disabled?: boolean;
  ariaLabel?: string;
}

export function TierPicker({
  value,
  onChange,
  disabled = false,
  ariaLabel,
}: TierPickerProps): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel ?? "Maturity tier"}
      className="flex flex-wrap gap-1"
    >
      {TIER_SHORT_LABELS.map(({ tier, label }) => {
        const active = value === tier;
        return (
          <button
            key={tier}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(active ? null : tier)}
            className={[
              "rounded-md px-2.5 py-1 text-xs font-semibold border transition",
              active
                ? "border-brand-500 bg-brand-500 text-ink-on-accent"
                : "border-border bg-surface-card text-ink-secondary hover:bg-surface-sunken",
              disabled ? "cursor-not-allowed opacity-50" : "",
            ].join(" ")}
            title={label}
          >
            T{tier}
            <span className="ml-1 hidden font-medium normal-case sm:inline">
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
