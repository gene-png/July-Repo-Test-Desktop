import type { JSX } from "react";
/**
 * Small inline "simulated" pill shown next to an AI result when the run used
 * deterministic demo fixtures (response mode === "fixture") rather than a live
 * provider call. Render it only when mode is "fixture".
 */
export function SimulatedBadge(): JSX.Element {
  return (
    <span
      title="This output came from deterministic demo fixtures, not a live AI call."
      className="inline-flex items-center gap-1 rounded-full bg-status-warning-bg px-2 py-0.5 text-xs font-semibold text-status-warning-fg"
    >
      <span aria-hidden>●</span> simulated
    </span>
  );
}
