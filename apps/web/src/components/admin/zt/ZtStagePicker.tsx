"use client";
import * as React from "react";

import type { CatalogStage } from "@/lib/zt/types";

import type { JSX } from "react";

export interface ZtStagePickerProps {
  value: number | null;
  onChange: (next: number | null) => void;
  disabled?: boolean;
  ariaLabel?: string;
  stages: CatalogStage[];
}

export function ZtStagePicker({
  value,
  onChange,
  disabled = false,
  ariaLabel,
  stages,
}: ZtStagePickerProps): JSX.Element {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel ?? "Maturity stage"}
      className="flex flex-wrap gap-1"
    >
      {stages.map(({ stage, label }) => {
        const active = value === stage;
        return (
          <button
            key={stage}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(active ? null : stage)}
            className={[
              "rounded-md px-2.5 py-1 text-xs font-semibold border transition",
              active
                ? "border-brand-500 bg-brand-500 text-ink-on-accent"
                : "border-border bg-surface-card text-ink-secondary hover:bg-surface-sunken",
              disabled ? "cursor-not-allowed opacity-50" : "",
            ].join(" ")}
            title={label}
          >
            S{stage}
            <span className="ml-1 hidden font-medium normal-case sm:inline">
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}
