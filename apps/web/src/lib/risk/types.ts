/** One source assessment feeding the register, with its approval status (S3-B). */
export interface RiskGateSource {
  kind: string; // attack | csf | zt
  status: string | null;
  approved: boolean;
}

export interface RiskGate {
  unlocked: boolean;
  has_attack: boolean;
  has_csf: boolean;
  has_zt: boolean;
  missing: string[];
  sources: RiskGateSource[];
}

export interface RiskEntry {
  id: string;
  title: string;
  description: string | null;
  axis: string | null;
  source: string | null;
  source_id: string | null;
  linked_techniques: string[] | null;
  linked_controls: string[] | null;
  likelihood: string | null;
  impact: string | null;
  tier: string | null;
  compensating_controls: string | null;
  residual_risk: string | null;
  recommended_action: string | null;
  rationale: string | null;
  origin: string;
  trust: string | null;
  locked: boolean;
}

/** PATCH body for /risk/entries/{id}; tier is always re-derived server-side. */
export interface RiskEntryPatch {
  title?: string;
  description?: string | null;
  likelihood?: string | null;
  impact?: string | null;
  compensating_controls?: string | null;
  recommended_action?: string | null;
  rationale?: string | null;
  locked?: boolean;
}

export interface RiskRegister {
  id: string;
  client_id: string;
  version: number;
  generated_by: string | null;
  finalized_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
  created_at: string;
  xlsx_artifact_id: string | null;
  pdf_artifact_id: string | null;
  docx_artifact_id: string | null;
  xlsx_filename: string | null;
  pdf_filename: string | null;
  docx_filename: string | null;
  entries: RiskEntry[];
  tier_counts: Record<string, number>;
  axis_counts: Record<string, number>;
  action_counts: Record<string, number>;
  warnings: string[];
  mode: string | null;
}
