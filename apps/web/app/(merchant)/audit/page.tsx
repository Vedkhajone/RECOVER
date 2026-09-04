"use client";

import { AlertTriangle, Copy, RefreshCw, Search, Webhook } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import * as React from "react";

import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Mono,
  Select,
  Spinner,
  TBody,
  THead,
  Table,
} from "@/components/ui/primitives";
import {
  api,
  type AuditEntry,
  type ExceptionRow,
  type WebhookEventRow,
} from "@/lib/api";
import { cn, formatDateTime, humanise } from "@/lib/utils";

const TABS = [
  { key: "audit", label: "Audit log" },
  { key: "webhooks", label: "Webhook events" },
  { key: "exceptions", label: "Exceptions" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

const STATUS_TONE: Record<string, "neutral" | "good" | "warn" | "bad" | "info"> = {
  ok: "neutral",
  duplicate: "warn",
  blocked: "bad",
  rejected: "bad",
  error: "bad",
  degraded: "warn",
};

const ACTOR_TONE: Record<string, "neutral" | "good" | "warn" | "bad" | "info"> = {
  ai_agent: "info",
  policy_engine: "warn",
  provider: "info",
  merchant: "neutral",
  customer: "good",
  system: "neutral",
};

function AuditTab() {
  const [rows, setRows] = React.useState<AuditEntry[]>([]);
  const [search, setSearch] = React.useState("");
  const [actor, setActor] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      setRows(
        await api.audit({
          search: search || undefined,
          actor: actor || undefined,
          limit: 300,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [search, actor]);

  React.useEffect(() => {
    const timer = setTimeout(() => void load(), 200);
    return () => clearTimeout(timer);
  }, [load]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit trail</CardTitle>
        <CardDescription>
          Every decision, tool call, policy verdict and provider interaction. Append-only.
          Credentials are redacted before anything is written here.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        <div className="flex flex-wrap gap-3 px-5 pb-4">
          <div className="relative min-w-[240px] flex-1">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-ink-500" />
            <Input
              className="pl-8"
              placeholder="Action, reason, case id"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="w-44">
            <Select value={actor} onChange={(e) => setActor(e.target.value)}>
              <option value="">All actors</option>
              {["system", "ai_agent", "policy_engine", "merchant", "customer", "provider"].map(
                (a) => (
                  <option key={a} value={a}>
                    {humanise(a)}
                  </option>
                ),
              )}
            </Select>
          </div>
          <Button size="sm" variant="ghost" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </Button>
        </div>

        {loading ? (
          <LoadingState label="Loading audit trail" />
        ) : error ? (
          <ErrorState detail={error} onRetry={() => void load()} />
        ) : rows.length === 0 ? (
          <EmptyState title="No audit entries match" />
        ) : (
          <Table>
            <THead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Case</th>
                <th>Policy</th>
                <th>Detail</th>
                <th>Status</th>
              </tr>
            </THead>
            <TBody>
              {rows.map((row) => (
                <tr key={row.id} className="align-top hover:bg-ink-850/40">
                  <td className="tnum whitespace-nowrap text-[11px] text-ink-500">
                    {formatDateTime(row.at)}
                  </td>
                  <td>
                    <Badge tone={ACTOR_TONE[row.actor] ?? "neutral"}>
                      {humanise(row.actor)}
                    </Badge>
                  </td>
                  <td>
                    <Mono className="text-ink-200">{row.action}</Mono>
                    {row.tool ? <Mono className="mt-0.5 block">{row.tool}</Mono> : null}
                  </td>
                  <td>
                    {row.case_id ? (
                      <Link
                        href={`/cases/${row.case_id}`}
                        className="text-[11px] text-accent-300 hover:text-accent-200"
                      >
                        {row.case_id}
                      </Link>
                    ) : (
                      <span className="text-ink-600">—</span>
                    )}
                  </td>
                  <td>
                    {row.policy_decision ? (
                      <Badge
                        tone={
                          row.policy_decision === "ALLOW"
                            ? "good"
                            : row.policy_decision === "BLOCK"
                              ? "bad"
                              : "warn"
                        }
                      >
                        {humanise(row.policy_decision)}
                      </Badge>
                    ) : (
                      <span className="text-ink-600">—</span>
                    )}
                  </td>
                  <td className="max-w-[380px]">
                    {row.reason ? (
                      <p className="text-[11px] leading-snug text-ink-400">{row.reason}</p>
                    ) : null}
                    {row.result ? (
                      <Mono className="mt-0.5 block truncate">{row.result}</Mono>
                    ) : null}
                    {row.error ? (
                      <p className="mt-0.5 text-[11px] text-bad-300">{row.error}</p>
                    ) : null}
                  </td>
                  <td>
                    <Badge tone={STATUS_TONE[row.status] ?? "neutral"}>{row.status}</Badge>
                  </td>
                </tr>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function WebhookTab() {
  const [rows, setRows] = React.useState<WebhookEventRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [flash, setFlash] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      setRows(await api.webhookEvents());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function replay(eventId: string) {
    setBusy(eventId);
    setFlash(null);
    try {
      const result = (await api.replayWebhook(eventId)) as Record<string, unknown>;
      setFlash(
        result.duplicate
          ? `Duplicate event detected — safely ignored. Delivery count is now ${result.delivery_count}; the business effect ran once.`
          : `Processed: ${String(result.effect)}`,
      );
      await load();
    } catch (e) {
      setFlash(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Webhook className="h-4 w-4 text-accent-400" /> Webhook idempotency ledger
        </CardTitle>
        <CardDescription>
          Every delivery is signature-verified against the raw body, then keyed on its
          event id. Replay a delivery to see the duplicate suppressed — the second one is
          counted, acknowledged with 200, and never re-applied.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {flash ? (
          <div className="mx-5 mb-3 animate-in rounded-lg border border-warn-500/30 bg-warn-500/10 px-3 py-2 text-xs text-warn-300">
            {flash}
          </div>
        ) : null}
        {loading ? (
          <LoadingState label="Loading webhook events" />
        ) : error ? (
          <ErrorState detail={error} onRetry={() => void load()} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No webhook deliveries yet"
            detail="Complete a payment on a customer recovery page and it will appear here."
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <th>Event id</th>
                <th>Event</th>
                <th>Signature</th>
                <th>Deliveries</th>
                <th>Business effect</th>
                <th>First seen</th>
                <th />
              </tr>
            </THead>
            <TBody>
              {rows.map((row) => (
                <tr key={row.event_id} className="hover:bg-ink-850/40">
                  <td>
                    <Mono className="text-ink-300">{row.event_id}</Mono>
                  </td>
                  <td className="text-xs text-ink-300">{row.event}</td>
                  <td>
                    <Badge tone={row.signature_verified ? "good" : "bad"}>
                      {row.signature_verified ? "Verified" : "Unverified"}
                    </Badge>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <span className="tnum text-ink-200">{row.delivery_count}</span>
                      {row.duplicate ? <Badge tone="warn">Duplicate seen</Badge> : null}
                    </div>
                  </td>
                  <td className="max-w-[280px] text-[11px] text-ink-400">
                    {row.effect ?? "—"}
                    {row.duplicate ? (
                      <span className="mt-0.5 block text-warn-300">
                        Applied once. Later deliveries were ignored.
                      </span>
                    ) : null}
                  </td>
                  <td className="tnum whitespace-nowrap text-[11px] text-ink-500">
                    {formatDateTime(row.first_received_at)}
                  </td>
                  <td>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busy !== null}
                      onClick={() => void replay(row.event_id)}
                    >
                      {busy === row.event_id ? (
                        <Spinner className="h-3 w-3" />
                      ) : (
                        <Copy className="h-3 w-3" />
                      )}
                      Replay
                    </Button>
                  </td>
                </tr>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function ExceptionsTab() {
  const [rows, setRows] = React.useState<ExceptionRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .exceptions()
      .then(setRows)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-warn-400" /> Unresolved exceptions
        </CardTitle>
        <CardDescription>
          Cases the system could not confidently resolve. They are surfaced here rather
          than quietly closed — an exception you cannot see is worse than one you can.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        {loading ? (
          <LoadingState label="Loading exceptions" />
        ) : error ? (
          <ErrorState detail={error} />
        ) : rows.length === 0 ? (
          <EmptyState
            title="No unresolved exceptions"
            detail="Run the reconciliation sweep from the dashboard to check for payments captured against stale orders."
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <th>Kind</th>
                <th>Amount</th>
                <th>Detail</th>
                <th>Case</th>
                <th>Raised</th>
              </tr>
            </THead>
            <TBody>
              {rows.map((row) => (
                <tr key={row.id} className="align-top hover:bg-ink-850/40">
                  <td>
                    <Badge tone="warn">{humanise(row.kind)}</Badge>
                  </td>
                  <td className="tnum whitespace-nowrap text-ink-200">
                    {row.amount.display}
                  </td>
                  <td className="max-w-[520px] text-xs leading-snug text-ink-400">
                    {row.detail}
                  </td>
                  <td>
                    {row.case_id ? (
                      <Link
                        href={`/cases/${row.case_id}`}
                        className="text-[11px] text-accent-300 hover:text-accent-200"
                      >
                        {row.case_id}
                      </Link>
                    ) : (
                      <span className="text-ink-600">—</span>
                    )}
                  </td>
                  <td className="tnum whitespace-nowrap text-[11px] text-ink-500">
                    {formatDateTime(row.at)}
                  </td>
                </tr>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function AuditScreen() {
  // Read the requested tab during the first render, not in an effect. Choosing
  // it afterwards paints the wrong tab for a frame, which users see as a flash
  // and tests see as an intermittent failure.
  const params = useSearchParams();
  const requested = params.get("tab");
  const initial: TabKey =
    requested && TABS.some((t) => t.key === requested) ? (requested as TabKey) : "audit";
  const [tab, setTab] = React.useState<TabKey>(initial);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink-100">
          Audit &amp; exceptions
        </h1>
        <p className="mt-1 text-xs text-ink-500">
          The complete record of what the system did and why.
        </p>
      </div>

      <div className="flex gap-1 border-b border-ink-850">
        {TABS.map((entry) => (
          <button
            key={entry.key}
            onClick={() => setTab(entry.key)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-xs font-medium transition-colors",
              tab === entry.key
                ? "border-accent-500 text-ink-100"
                : "border-transparent text-ink-500 hover:text-ink-300",
            )}
          >
            {entry.label}
          </button>
        ))}
      </div>

      {tab === "audit" ? <AuditTab /> : null}
      {tab === "webhooks" ? <WebhookTab /> : null}
      {tab === "exceptions" ? <ExceptionsTab /> : null}
    </div>
  );
}

export default function AuditPage() {
  return (
    <React.Suspense fallback={<LoadingState label="Loading audit trail" />}>
      <AuditScreen />
    </React.Suspense>
  );
}
