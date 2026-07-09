"use client";
import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  NumberCard,
  StatusPill,
  type DataTableColumn,
} from "@shield/design-system";

import { ClientSwitcher } from "@/components/site/ClientSwitcher";
import {
  approveRiskRegister,
  deleteRiskEntry,
  describeRiskError,
  exportRiskRegister,
  fetchRiskGate,
  fetchRiskRegisterLatest,
  generateRiskRegister,
  getActiveClientId,
  getClientName,
  patchRiskEntry,
} from "@/lib/risk/client";
import {
  IMPACTS,
  LIKELIHOODS,
  TIER_COLOR,
  isImpact,
  isLikelihood,
  tierFor,
  titleCase,
  type RiskTier,
} from "@/lib/risk/matrix";
import type {
  RiskEntry,
  RiskGate,
  RiskGateSource,
  RiskRegister,
} from "@/lib/risk/types";

import type { JSX } from "react";

const RECOMMENDED_ACTIONS = [
  "remediate",
  "mitigate",
  "accept",
  "transfer",
  "avoid",
] as const;

const SOURCE_KIND_LABEL: Record<string, string> = {
  attack: "MITRE ATT&CK",
  csf: "NIST CSF",
  zt: "Zero Trust",
};

function TierChip({ tier }: { tier: string | null }): JSX.Element {
  const t = (tier ?? "negligible") as RiskTier;
  const color = TIER_COLOR[t] ?? TIER_COLOR.negligible;
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold"
      style={{ backgroundColor: color.bg, color: color.fg }}
    >
      {titleCase(tier)}
    </span>
  );
}

/** Approval status of each source assessment feeding the register (S3-B gate). */
function SourcesBar({ sources }: { sources: RiskGateSource[] }): JSX.Element {
  if (sources.length === 0) {
    return (
      <p className="text-sm text-ink-tertiary">
        No source assessments found yet.
      </p>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm text-ink-secondary">Sources:</span>
      {sources.map((s) => (
        <StatusPill
          key={s.kind}
          tone={s.approved ? "success" : "warning"}
          withDot
        >
          {SOURCE_KIND_LABEL[s.kind] ?? s.kind} ·{" "}
          {s.approved ? "approved" : (s.status ?? "not approved")}
        </StatusPill>
      ))}
    </div>
  );
}

function Matrix({ entries }: { entries: RiskEntry[] }): JSX.Element {
  const counts = new Map<string, number>();
  for (const e of entries) {
    if (isLikelihood(e.likelihood) && isImpact(e.impact)) {
      const key = `${e.likelihood}|${e.impact}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
  }
  const rows = [...LIKELIHOODS].reverse();
  return (
    <div className="overflow-x-auto">
      <table className="border-collapse text-center text-xs">
        <thead>
          <tr>
            <th className="p-2" />
            {IMPACTS.map((im) => (
              <th key={im} className="p-2 font-medium text-ink-secondary">
                {titleCase(im)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((lk) => (
            <tr key={lk}>
              <th className="whitespace-nowrap p-2 text-right font-medium text-ink-secondary">
                {titleCase(lk)}
              </th>
              {IMPACTS.map((im) => {
                const color = TIER_COLOR[tierFor(lk, im)];
                const n = counts.get(`${lk}|${im}`) ?? 0;
                return (
                  <td
                    key={im}
                    className="h-12 w-16 border border-white text-sm font-semibold"
                    style={{ backgroundColor: color.bg, color: color.fg }}
                    title={`${titleCase(lk)} × ${titleCase(im)}`}
                  >
                    {n > 0 ? n : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DownloadLink({
  id,
  filename,
  label,
}: {
  id: string | null;
  filename: string | null;
  label: string;
}): JSX.Element | null {
  if (!id) return null;
  return (
    <a
      href={`/api/proxy/artifacts/${id}/download`}
      className="rounded-md border border-border-default px-3 py-1.5 text-sm font-medium text-ink-primary hover:bg-surface-muted"
    >
      {label}
      {filename ? (
        <span className="ml-1 text-ink-tertiary">({filename})</span>
      ) : null}
    </a>
  );
}

interface EditForm {
  title: string;
  likelihood: string;
  impact: string;
  compensating_controls: string;
  recommended_action: string;
  rationale: string;
}

function EditEntryDialog({
  entry,
  saving,
  error,
  onCancel,
  onSave,
}: {
  entry: RiskEntry;
  saving: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (form: EditForm) => void;
}): JSX.Element {
  const [form, setForm] = React.useState<EditForm>({
    title: entry.title ?? "",
    likelihood: entry.likelihood ?? "",
    impact: entry.impact ?? "",
    compensating_controls: entry.compensating_controls ?? "",
    recommended_action: entry.recommended_action ?? "",
    rationale: entry.rationale ?? "",
  });

  function set<K extends keyof EditForm>(key: K, value: EditForm[K]): void {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  const derivedTier =
    isLikelihood(form.likelihood) && isImpact(form.impact)
      ? tierFor(form.likelihood, form.impact)
      : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Edit risk entry"
    >
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-lg bg-surface-card p-6 shadow-xl">
        <h2 className="text-lg font-semibold text-ink-primary">
          Edit risk entry
        </h2>
        <p className="mt-1 text-sm text-ink-secondary">
          Tier is re-derived from likelihood × impact on save.
        </p>

        <div className="mt-4 flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-ink-primary">Weakness</span>
            <input
              type="text"
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
              className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
            />
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Likelihood</span>
              <select
                value={form.likelihood}
                onChange={(e) => set("likelihood", e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              >
                <option value="">—</option>
                {LIKELIHOODS.map((l) => (
                  <option key={l} value={l}>
                    {titleCase(l)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-ink-primary">Impact</span>
              <select
                value={form.impact}
                onChange={(e) => set("impact", e.target.value)}
                className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
              >
                <option value="">—</option>
                {IMPACTS.map((i) => (
                  <option key={i} value={i}>
                    {titleCase(i)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <p className="text-sm text-ink-secondary">
            Derived tier: {derivedTier ? <TierChip tier={derivedTier} /> : "—"}
          </p>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-ink-primary">
              Recommended action
            </span>
            <select
              value={form.recommended_action}
              onChange={(e) => set("recommended_action", e.target.value)}
              className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
            >
              <option value="">—</option>
              {RECOMMENDED_ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {titleCase(a)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-ink-primary">
              Compensating controls
            </span>
            <textarea
              rows={2}
              value={form.compensating_controls}
              onChange={(e) => set("compensating_controls", e.target.value)}
              className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-ink-primary">Rationale</span>
            <textarea
              rows={2}
              value={form.rationale}
              onChange={(e) => set("rationale", e.target.value)}
              className="rounded-md border border-border bg-surface-card px-3 py-2 text-ink-primary focus:border-brand-500 focus:outline-none"
            />
          </label>

          {error ? (
            <p className="text-sm text-status-danger-fg" role="alert">
              {error}
            </p>
          ) : null}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={saving}
            className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave(form)}
            disabled={saving}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function RiskRegisterDashboard(): JSX.Element {
  const [cid, setCid] = React.useState<string | null>(null);
  const [clientName, setClientName] = React.useState("Client");
  const [gate, setGate] = React.useState<RiskGate | null>(null);
  const [register, setRegister] = React.useState<RiskRegister | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [busy, setBusy] = React.useState<
    "generate" | "export" | "approve" | null
  >(null);
  const [error, setError] = React.useState<string | null>(null);
  const [rowBusyId, setRowBusyId] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<RiskEntry | null>(null);
  const [editError, setEditError] = React.useState<string | null>(null);
  const [savingEdit, setSavingEdit] = React.useState(false);
  const [reloadKey, setReloadKey] = React.useState(0);

  React.useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const id = await getActiveClientId();
        if (!active) return;
        setCid(id);
        if (!id) {
          setLoading(false);
          return;
        }
        const [name, g, reg] = await Promise.all([
          getClientName(id),
          fetchRiskGate(id),
          fetchRiskRegisterLatest(id),
        ]);
        if (!active) return;
        setClientName(name);
        setGate(g);
        setRegister(reg);
      } catch (err) {
        if (active) setError(describeRiskError(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const reloadRegister = React.useCallback(async () => {
    if (!cid) return;
    const reg = await fetchRiskRegisterLatest(cid);
    setRegister(reg);
  }, [cid]);

  async function onGenerate(): Promise<void> {
    if (!cid) return;
    setBusy("generate");
    setError(null);
    try {
      setRegister(await generateRiskRegister(cid));
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onExport(): Promise<void> {
    if (!cid) return;
    setBusy("export");
    setError(null);
    try {
      setRegister(await exportRiskRegister(cid));
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onApprove(): Promise<void> {
    if (!cid) return;
    if (
      !window.confirm(
        "Approve this Risk Register version? It freezes the entries and " +
          "enables export. Generate a new version to make further edits.",
      )
    ) {
      return;
    }
    setBusy("approve");
    setError(null);
    try {
      setRegister(await approveRiskRegister(cid));
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setBusy(null);
    }
  }

  async function onToggleLock(entry: RiskEntry): Promise<void> {
    setRowBusyId(entry.id);
    setError(null);
    try {
      await patchRiskEntry(entry.id, { locked: !entry.locked });
      await reloadRegister();
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setRowBusyId(null);
    }
  }

  async function onDelete(entry: RiskEntry): Promise<void> {
    if (
      !window.confirm(
        `Delete "${entry.title}"? It is removed from the register and its ` +
          `exports. This is a soft delete.`,
      )
    ) {
      return;
    }
    setRowBusyId(entry.id);
    setError(null);
    try {
      await deleteRiskEntry(entry.id);
      await reloadRegister();
    } catch (err) {
      setError(describeRiskError(err));
    } finally {
      setRowBusyId(null);
    }
  }

  async function onSaveEdit(form: EditForm): Promise<void> {
    if (!editing) return;
    setSavingEdit(true);
    setEditError(null);
    try {
      await patchRiskEntry(editing.id, {
        title: form.title,
        likelihood: form.likelihood || null,
        impact: form.impact || null,
        compensating_controls: form.compensating_controls || null,
        recommended_action: form.recommended_action || null,
        rationale: form.rationale || null,
      });
      await reloadRegister();
      setEditing(null);
    } catch (err) {
      setEditError(describeRiskError(err));
    } finally {
      setSavingEdit(false);
    }
  }

  const approved = register?.approved_at != null;

  const columns: DataTableColumn<RiskEntry>[] = [
    {
      key: "title",
      header: "Weakness",
      cell: (r) => (
        <span className="flex items-center gap-2">
          {r.locked ? (
            <span
              title="Locked — preserved verbatim on regenerate"
              aria-label="Locked"
            >
              🔒
            </span>
          ) : null}
          {r.title}
        </span>
      ),
    },
    { key: "axis", header: "Axis", cell: (r) => titleCase(r.axis) },
    {
      key: "li",
      header: "Likelihood × Impact",
      cell: (r) => `${titleCase(r.likelihood)} × ${titleCase(r.impact)}`,
    },
    { key: "tier", header: "Tier", cell: (r) => <TierChip tier={r.tier} /> },
    {
      key: "action",
      header: "Recommended",
      cell: (r) => titleCase(r.recommended_action),
    },
    {
      key: "actions",
      header: "Actions",
      cell: (r) =>
        approved ? (
          <span className="text-xs text-ink-tertiary">Locked</span>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                setEditError(null);
                setEditing(r);
              }}
              disabled={rowBusyId === r.id}
              className="rounded-md border border-border-default px-2 py-1 text-xs font-medium text-ink-primary hover:bg-surface-muted disabled:opacity-50"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={() => void onToggleLock(r)}
              disabled={rowBusyId === r.id}
              className="rounded-md border border-border-default px-2 py-1 text-xs font-medium text-ink-primary hover:bg-surface-muted disabled:opacity-50"
            >
              {r.locked ? "Unlock" : "Lock"}
            </button>
            <button
              type="button"
              onClick={() => void onDelete(r)}
              disabled={rowBusyId === r.id}
              className="rounded-md border border-status-danger-border px-2 py-1 text-xs font-medium text-status-danger-fg hover:bg-status-danger-bg disabled:opacity-50"
            >
              Delete
            </button>
          </div>
        ),
    },
  ];

  if (loading) {
    return <p className="text-sm text-ink-secondary">Loading…</p>;
  }

  if (!cid) {
    return (
      <EmptyState
        title="Pick a client first"
        description="The Risk Register is generated per client. Choose a client to continue."
        action={
          <div className="flex items-center gap-2">
            <span className="text-sm text-ink-secondary">Pick a client:</span>
            <ClientSwitcher onChanged={() => setReloadKey((k) => k + 1)} />
          </div>
        }
      />
    );
  }

  if (gate && !gate.unlocked) {
    return (
      <div className="flex flex-col gap-4">
        <EmptyState
          title="Risk Register is locked"
          description={`To synthesise risks for ${clientName}, first complete: ${gate.missing.join("; ")}.`}
        />
        <Card>
          <CardHeader>
            <CardTitle>Source assessments</CardTitle>
            <CardDescription>
              The register unlocks once ATT&amp;CK is approved plus at least one
              approved CSF or Zero Trust assessment.
            </CardDescription>
          </CardHeader>
          <CardBody>
            <SourcesBar sources={gate.sources} />
          </CardBody>
        </Card>
      </div>
    );
  }

  const tc = register?.tier_counts ?? {};
  const ac = register?.axis_counts ?? {};

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink-primary">
            Risk Register
          </h1>
          <p className="mt-1 text-sm text-ink-secondary">
            {clientName}
            {register
              ? ` · version ${register.version}`
              : " · not yet generated"}
            {approved ? " · approved" : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onGenerate}
            disabled={busy !== null}
            className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:opacity-50"
          >
            {busy === "generate"
              ? "Generating…"
              : register
                ? "Regenerate"
                : "Generate"}
          </button>
          {register && !approved ? (
            <button
              type="button"
              onClick={onApprove}
              disabled={busy !== null}
              className="rounded-md border border-status-success-border bg-status-success-bg px-4 py-2 text-sm font-semibold text-status-success-fg hover:opacity-90 disabled:opacity-50"
            >
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
          ) : null}
          {register ? (
            <button
              type="button"
              onClick={onExport}
              disabled={busy !== null || !approved}
              title={
                approved ? undefined : "Approve the register before exporting."
              }
              className="rounded-md border border-border-default px-4 py-2 text-sm font-semibold text-ink-primary hover:bg-surface-muted disabled:opacity-50"
            >
              {busy === "export" ? "Exporting…" : "Export XLSX / PDF / Word"}
            </button>
          ) : null}
        </div>
      </div>

      {gate ? <SourcesBar sources={gate.sources} /> : null}

      {error ? (
        <p className="rounded-md bg-status-danger-bg px-3 py-2 text-sm text-status-danger-fg">
          {error}
        </p>
      ) : null}

      {register && register.warnings.length > 0 ? (
        <div
          role="status"
          className="rounded-md border border-status-warning-border bg-status-warning-bg px-3 py-2 text-sm text-status-warning-fg"
        >
          <p className="font-semibold">Generation warnings</p>
          <ul className="mt-1 list-inside list-disc">
            {register.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {approved ? (
        <p className="rounded-md border border-status-success-border bg-status-success-bg px-3 py-2 text-sm text-status-success-fg">
          This version is approved and frozen. Generate a new version to edit
          entries.
        </p>
      ) : null}

      {!register ? (
        <Card>
          <CardBody>
            <p className="text-sm text-ink-secondary">
              No Risk Register yet. Generate one to synthesise the client&apos;s
              ATT&amp;CK, CSF, and Zero Trust gaps into a tiered register.
            </p>
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <NumberCard label="Entries" value={register.entries.length} />
            <NumberCard
              label="Critical + High"
              value={(tc.critical ?? 0) + (tc.high ?? 0)}
              deltaTone="negative"
            />
            <NumberCard label="Detection" value={ac.detection ?? 0} />
            <NumberCard label="Prevention" value={ac.prevention ?? 0} />
            <NumberCard label="Response" value={ac.response ?? 0} />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Likelihood × Impact</CardTitle>
              <CardDescription>
                NIST 800-30 5×5. Each cell counts the entries that land there;
                colour is the derived tier.
              </CardDescription>
            </CardHeader>
            <CardBody>
              <Matrix entries={register.entries} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Register</CardTitle>
              <CardDescription>
                Tier is always code-derived from likelihood × impact. Lock an
                entry to preserve it verbatim when you regenerate.
              </CardDescription>
            </CardHeader>
            <CardBody className="flex flex-col gap-4">
              <DataTable
                columns={columns}
                rows={register.entries}
                rowKey={(r) => r.id}
                emptyState={
                  <p className="p-4 text-sm text-ink-secondary">
                    No entries — the synthesis found no open gaps.
                  </p>
                }
              />
              {register.xlsx_artifact_id ||
              register.pdf_artifact_id ||
              register.docx_artifact_id ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm text-ink-secondary">Downloads:</span>
                  <DownloadLink
                    id={register.xlsx_artifact_id}
                    filename={register.xlsx_filename}
                    label="XLSX"
                  />
                  <DownloadLink
                    id={register.pdf_artifact_id}
                    filename={register.pdf_filename}
                    label="PDF"
                  />
                  <DownloadLink
                    id={register.docx_artifact_id}
                    filename={register.docx_filename}
                    label="Word"
                  />
                </div>
              ) : (
                <p className="text-sm text-ink-tertiary">
                  {approved
                    ? "Export to generate downloadable XLSX / PDF / Word files."
                    : "Approve the register to enable export."}
                </p>
              )}
            </CardBody>
          </Card>
        </>
      )}

      {editing ? (
        <EditEntryDialog
          entry={editing}
          saving={savingEdit}
          error={editError}
          onCancel={() => setEditing(null)}
          onSave={(form) => void onSaveEdit(form)}
        />
      ) : null}
    </div>
  );
}
