"use client";
import * as React from "react";

import {
  Card,
  CardBody,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@shield/design-system";

import {
  describeMessagesError,
  fetchMessages,
  postMessage,
  type MessageRow,
} from "@/lib/messages/client";

import type { JSX } from "react";

export interface MessageThreadProps {
  serviceId: string;
  /** Heading override; defaults to "Messages". */
  title?: string;
}

function authorLabel(role: string | null): string {
  if (role === "admin") return "SHIELD analyst";
  if (role === "client") return "Client";
  return "Participant";
}

function fmtTime(value: string): string {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export function MessageThread({
  serviceId,
  title = "Messages",
}: MessageThreadProps): JSX.Element {
  const [messages, setMessages] = React.useState<MessageRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [draft, setDraft] = React.useState("");
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const { messages: rows } = await fetchMessages(serviceId);
      setMessages(rows);
    } catch (err) {
      setError(describeMessagesError(err));
    } finally {
      setLoading(false);
    }
  }, [serviceId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function onSend(): Promise<void> {
    const text = draft.trim();
    if (!text) return;
    setSending(true);
    setError(null);
    try {
      const created = await postMessage(serviceId, text);
      setMessages((prev) => [...prev, created]);
      setDraft("");
    } catch (err) {
      setError(describeMessagesError(err));
    } finally {
      setSending(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>
          A shared thread for this assessment. Use it to ask for, or provide,
          more information without leaving the workspace.
        </CardDescription>
      </CardHeader>
      <CardBody className="flex flex-col gap-4">
        {error ? (
          <p className="text-sm text-status-danger-fg" role="alert">
            {error}
          </p>
        ) : null}

        <div className="flex max-h-96 flex-col gap-3 overflow-y-auto">
          {loading ? (
            <p className="text-sm text-ink-tertiary">Loading…</p>
          ) : messages.length === 0 ? (
            <p className="text-sm text-ink-secondary">
              No messages yet. Start the conversation below.
            </p>
          ) : (
            messages.map((m) => (
              <div
                key={m.id}
                className="rounded-lg border border-border-subtle bg-surface-card px-3 py-2"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-semibold text-ink-primary">
                    {authorLabel(m.author_role)}
                  </span>
                  <span className="text-xs text-ink-tertiary">
                    {fmtTime(m.created_at)}
                  </span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-sm text-ink-secondary">
                  {m.body}
                </p>
              </div>
            ))
          )}
        </div>

        <div className="flex flex-col gap-2">
          <label htmlFor={`msg-${serviceId}`} className="sr-only">
            Write a message
          </label>
          <textarea
            id={`msg-${serviceId}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            placeholder="Write a message…"
            className="w-full rounded-md border border-border-default bg-surface-card px-3 py-2 text-sm text-ink-primary focus:border-brand-500 focus:outline-none"
          />
          <div>
            <button
              type="button"
              onClick={() => void onSend()}
              disabled={sending || draft.trim().length === 0}
              className="rounded-md bg-brand-500 px-4 py-2 text-sm font-semibold text-ink-on-accent hover:bg-brand-600 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
