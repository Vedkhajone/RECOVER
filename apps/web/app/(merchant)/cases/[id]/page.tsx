"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  Bot,
  Check,
  CheckCircle2,
  Clock,
  CreditCard,
  ExternalLink,
  FlaskConical,
  Play,
  ShieldCheck,
  User,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import * as React from "react";

import {
  ActionBadge,
  AIPathBadge,
  PolicyBadge,
  RecoverabilityBadge,
  StatusBadge,
} from "@/components/status";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  LoadingState,
  Mono,
  Select,
  Spinner,
} from "@/components/ui/primitives";
import { api, type CaseDetail, type PolicyProbe } from "@/lib/api";
import { cn, formatDateTime, formatPercent, humanise } from "@/lib/utils";

const ACTIONS = [
  "RETRY_PAYMENT",
  "SEND_PAYMENT_LINK",
  "SEND_REMINDER",
  "OFFER_ALLOWED_ALTERNATIVE",
  "ESCALATE_TO_MERCHANT",
  "WAIT",
  "STOP",
];

const ACTOR_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  system: Clock,
  ai_agent: Bot,
  policy_engine: ShieldCheck,
  merchant: User,
  customer: User,
  provider: CreditCard,
};

const ACTOR_COLOR: Record<string, string> = {
  system: "text-ink-400 border-ink-700 bg-ink-800",
  ai_agent: "text-accent-300 border-accent-500/30 bg-accent-500/10",
  policy_engine: "text-warn-300 border-warn-500/30 bg-warn-500/10",
  merchant: "text-ink-200 border-ink-600 bg-ink-800",
  customer: "text-good-300 border-good-500/30 bg-good-500/10",
  provider: "text-accent-300 border-accent-500/30 bg-accent-500/10",
};

function PanelRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="border-t border-ink-850 px-5 py-3 first:border-t-0">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-ink-500">
        {label}
      </p>
      <div className="mt-1.5 text-sm text-ink-200">{children}</div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-ink-500">{label}</p>
      <p className="mt-0.5 text-sm text-ink-200">{value}</p>
    </div>
  );
}

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const caseId = params.id;

  const [data, setData] = React.useState<CaseDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [flash, setFlash] = React.useState<{ text: string; bad?: boolean } | null>(null);
  const [probeAction, setProbeAction] = React.useState("RETRY_PAYMENT");
  const [probe, setProbe] = React.useState<PolicyProbe | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      setData(await api.case(caseId));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, fn: () => Promise<unknown>, message: string) {
    setBusy(key);
    setFlash(null);
    try {
      await fn();
      setFlash({ text: message });
      await load();
    } catch (e) {
      setFlash({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  }

  async function runProbe() {
    setBusy("probe");
    try {
      setProbe(await api.probePolicy(caseId, probeAction));
    } catch (e) {
      setFlash({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingState label="Loading case" />;
  if (error) return <ErrorState detail={error} onRetry={() => void load()} />;
  if (!data) return null;

  const panel = data.decision_panel;
  const recovered = data.recovered_amount.paise > 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            href="/cases"
            className="mb-2 inline-flex items-center gap-1 text-xs text-ink-500 hover:text-ink-300"
          >
            <ArrowLeft className="h-3 w-3" /> All cases
          </Link>
          <h1 className="flex flex-wrap items-center gap-2 text-lg font-semibold tracking-tight text-ink-100">
            {data.customer_name}
            <StatusBadge status={data.status} />
            {data.customer_risk_flagged ? <Badge tone="bad">Risk flagged</Badge> : null}
          </h1>
          <p className="mt-1 text-xs text-ink-500">
            {data.order_reference ?? "no order"} · {data.description ?? "—"} ·{" "}
            <Mono>{data.id}</Mono>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              void act("process", () => api.processCase(caseId), "Case reprocessed.")
            }
          >
            {busy === "process" ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            Run recovery cycle
          </Button>
          {data.status === "AWAITING_APPROVAL" ? (
            <>
              <Button
                size="sm"
                variant="good"
                disabled={busy !== null}
                onClick={() =>
                  void act("approve", () => api.approveCase(caseId), "Action approved and executed.")
                }
              >
                {busy === "approve" ? <Spinner className="h-3.5 w-3.5" /> : <Check className="h-3.5 w-3.5" />}
                Approve
              </Button>
              <Button
                size="sm"
                variant="danger"
                disabled={busy !== null}
                onClick={() =>
                  void act("reject", () => api.rejectCase(caseId), "Recovery declined and stopped.")
                }
              >
                <X className="h-3.5 w-3.5" /> Decline
              </Button>
            </>
          ) : null}
          {data.recovery_token ? (
            <Link href={`/recover/${data.recovery_token}`} target="_blank">
              <Button size="sm" variant="primary">
                <ExternalLink className="h-3.5 w-3.5" /> Customer recovery page
              </Button>
            </Link>
          ) : null}
        </div>
      </div>

      {flash ? (
        <div
          className={cn(
            "animate-in rounded-lg border px-4 py-2.5 text-xs",
            flash.bad
              ? "border-bad-500/30 bg-bad-500/10 text-bad-300"
              : "border-good-500/30 bg-good-500/10 text-good-300",
          )}
        >
          {flash.text}
        </div>
      ) : null}

      {recovered ? (
        <Card className="border-good-500/30 bg-good-500/[0.06]">
          <CardContent className="flex items-center gap-3 pt-4">
            <CheckCircle2 className="h-5 w-5 text-good-400" />
            <div>
              <p className="text-sm font-medium text-good-300">
                {data.recovered_amount.display} recovered
              </p>
              <p className="text-xs text-ink-400">
                Written from a signature-verified payment webhook, after{" "}
                {data.retry_count} recovery attempt
                {data.retry_count === 1 ? "" : "s"}.
              </p>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-3">
        {/* ------------------------------------------------ decision panel */}
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-accent-400" /> AI decision panel
            </CardTitle>
            <CardDescription>
              What happened, what the model proposed, and what policy actually permitted.
              Concise explanations and structured evidence only — no hidden reasoning.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            <PanelRow label="What happened">{panel.what_happened}</PanelRow>
            <PanelRow label="Why it happened">
              {panel.why_it_happened ?? (
                <span className="text-ink-500">Not yet diagnosed.</span>
              )}
            </PanelRow>
            <PanelRow label="What the AI recommends">
              <div className="flex flex-wrap items-center gap-2">
                <ActionBadge action={panel.what_ai_recommends} />
                <RecoverabilityBadge level={data.recoverability} />
                <AIPathBadge path={panel.ai_path} model={panel.ai_model} />
                {panel.ai_confidence !== null ? (
                  <span className="tnum text-xs text-ink-400">
                    confidence {formatPercent(panel.ai_confidence, 0)}
                  </span>
                ) : null}
              </div>
              {panel.ai_degraded ? (
                <p className="mt-2 flex items-start gap-1.5 rounded border border-warn-500/30 bg-warn-500/10 px-2.5 py-1.5 text-[11px] text-warn-300">
                  <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                  The model call did not return a valid decision, so the deterministic
                  fallback answered.{" "}
                  {panel.ai_validation_error ? (
                    <Mono className="text-warn-300/80">{panel.ai_validation_error}</Mono>
                  ) : null}
                </p>
              ) : null}
              {panel.ai_tool_calls.length > 0 ? (
                <p className="mt-2 text-[11px] text-ink-500">
                  Tools called: {panel.ai_tool_calls.join(", ")}
                </p>
              ) : null}
            </PanelRow>
            <PanelRow label="Why the AI recommends it">
              {panel.why_ai_recommends_it ?? (
                <span className="text-ink-500">No reasoning recorded.</span>
              )}
            </PanelRow>
            <PanelRow label="What policy allows">
              <div className="mt-1 space-y-1.5">
                {panel.what_policy_allows.map((entry) => (
                  <div
                    key={entry.action}
                    className="flex flex-wrap items-center gap-2 rounded border border-ink-850
                      bg-ink-850/40 px-2.5 py-1.5"
                  >
                    <span className="w-56 shrink-0 text-xs text-ink-300">
                      {humanise(entry.action)}
                    </span>
                    <PolicyBadge decision={entry.decision} />
                    <span className="min-w-0 flex-1 text-[11px] text-ink-500">
                      {entry.reason}
                    </span>
                    <span className="tnum shrink-0 text-[11px] text-ink-500">
                      EV {entry.expected_value.display}
                    </span>
                  </div>
                ))}
              </div>
            </PanelRow>
            <PanelRow label="What action was actually taken">
              <div className="flex flex-wrap items-center gap-2">
                <ActionBadge action={panel.what_action_was_taken} />
                {panel.what_action_was_taken === null &&
                data.ai_recommended_action &&
                data.policy_decision !== "ALLOW" ? (
                  <span className="text-xs text-ink-500">
                    The proposal was not executed — policy returned{" "}
                    {humanise(data.policy_decision)}.
                  </span>
                ) : null}
              </div>
            </PanelRow>
            <PanelRow label="Result">
              <div className="flex flex-wrap items-center gap-3">
                <span className={recovered ? "text-good-300" : "text-ink-200"}>
                  {panel.result}
                </span>
                <span className="tnum text-xs text-ink-500">
                  recovered {panel.recovered_amount.display}
                </span>
                <span className="tnum text-xs text-ink-500">
                  P(recover) {formatPercent(panel.recovery_probability, 0)}
                </span>
                <span className="tnum text-xs text-ink-500">
                  expected value {data.expected_value.display}
                </span>
              </div>
            </PanelRow>
          </CardContent>
        </Card>

        {/* ---------------------------------------------------- side panel */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Case facts</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <Fact label="Amount at risk" value={<span className="tnum">{data.amount.display}</span>} />
              <Fact label="Order state" value={humanise(data.order_state)} />
              <Fact label="Failure reason" value={humanise(data.failure_reason)} />
              <Fact label="Event type" value={humanise(data.event_type)} />
              <Fact label="Retries used" value={`${data.retry_count}`} />
              <Fact label="Contacts sent" value={`${data.contacts_sent}`} />
              <Fact
                label="Provider error"
                value={
                  data.provider_error_code ? (
                    <Mono>{data.provider_error_code}</Mono>
                  ) : (
                    <span className="text-ink-600">—</span>
                  )
                }
              />
              <Fact label="Opened" value={formatDateTime(data.opened_at)} />
              {data.provider_error_description ? (
                <div className="col-span-2">
                  <p className="text-[10px] uppercase tracking-wider text-ink-500">
                    Gateway message
                  </p>
                  <p className="mt-0.5 text-xs text-ink-400">
                    {data.provider_error_description}
                  </p>
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Customer</CardTitle>
              <CardDescription>{data.customer_email}</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <Fact label="Segment" value={humanise(data.customer_segment)} />
              <Fact
                label="Success rate"
                value={
                  <span className="tnum">{formatPercent(data.customer_success_rate, 0)}</span>
                }
              />
              <Fact
                label="Successful payments"
                value={<span className="tnum">{data.customer_successful_payments}</span>}
              />
              <Fact
                label="Failed payments"
                value={<span className="tnum">{data.customer_failed_payments}</span>}
              />
            </CardContent>
          </Card>

          {/* Scenario 7: probe the policy engine with any action. */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <FlaskConical className="h-4 w-4 text-warn-400" /> Policy probe
              </CardTitle>
              <CardDescription>
                Ask the real policy engine what it would say about any action on this case.
                It evaluates only — nothing is executed and nothing is attributed to the
                model.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <div className="flex gap-2">
                <Select
                  value={probeAction}
                  onChange={(e) => setProbeAction(e.target.value)}
                >
                  {ACTIONS.map((a) => (
                    <option key={a} value={a}>
                      {humanise(a)}
                    </option>
                  ))}
                </Select>
                <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void runProbe()}>
                  {busy === "probe" ? <Spinner className="h-3.5 w-3.5" /> : "Evaluate"}
                </Button>
              </div>
              {probe ? (
                <div
                  className={cn(
                    "animate-in rounded-lg border px-3 py-2.5",
                    probe.decision === "BLOCK"
                      ? "border-bad-500/30 bg-bad-500/10"
                      : probe.decision === "REQUIRE_APPROVAL"
                        ? "border-warn-500/30 bg-warn-500/10"
                        : "border-good-500/30 bg-good-500/10",
                  )}
                >
                  <div className="flex items-center gap-2">
                    {probe.decision === "BLOCK" ? (
                      <Ban className="h-3.5 w-3.5 text-bad-400" />
                    ) : (
                      <ShieldCheck className="h-3.5 w-3.5 text-good-400" />
                    )}
                    <PolicyBadge decision={probe.decision} />
                    <Mono>{probe.code}</Mono>
                  </div>
                  <p className="mt-1.5 text-[11px] leading-snug text-ink-300">
                    {probe.reason}
                  </p>
                </div>
              ) : null}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* ------------------------------------------------------- timeline */}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Recovery timeline</CardTitle>
            <CardDescription>
              Every entry is written after the fact it records. Nothing here is
              anticipated.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="relative space-y-0">
              {data.timeline.map((entry, index) => {
                const Icon = ACTOR_ICON[entry.actor] ?? Clock;
                const last = index === data.timeline.length - 1;
                return (
                  <li key={index} className="relative flex gap-3 pb-4 last:pb-0">
                    {!last ? (
                      <span className="absolute left-[13px] top-7 h-full w-px bg-ink-800" />
                    ) : null}
                    <span
                      className={cn(
                        "relative z-10 mt-0.5 flex h-[26px] w-[26px] shrink-0 items-center",
                        "justify-center rounded-full border",
                        ACTOR_COLOR[entry.actor] ?? ACTOR_COLOR.system,
                      )}
                    >
                      <Icon className="h-3 w-3" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-2">
                        <span className="tnum text-[11px] text-ink-500">
                          {formatDateTime(entry.at)}
                        </span>
                        <span className="text-sm text-ink-100">{entry.title}</span>
                      </div>
                      {entry.detail ? (
                        <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
                          {entry.detail}
                        </p>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recovery attempts</CardTitle>
            <CardDescription>
              Each attempt, its cost, and whether it worked.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {data.attempts.length === 0 ? (
              <p className="py-4 text-center text-xs text-ink-500">
                No recovery action has been taken on this case.
              </p>
            ) : (
              data.attempts.map((attempt, index) => (
                <div
                  key={index}
                  className="rounded-lg border border-ink-850 bg-ink-850/40 px-3 py-2.5"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <ActionBadge action={attempt.action} />
                    {attempt.succeeded === true ? (
                      <Badge tone="good">Succeeded</Badge>
                    ) : attempt.succeeded === false ? (
                      <Badge tone="bad">Failed</Badge>
                    ) : (
                      <Badge tone="neutral">Pending</Badge>
                    )}
                    <span className="tnum ml-auto text-[11px] text-ink-500">
                      {attempt.cost.display}
                    </span>
                  </div>
                  {attempt.detail ? (
                    <p className="mt-1 text-[11px] leading-snug text-ink-500">
                      {attempt.detail}
                    </p>
                  ) : null}
                  <Mono className="mt-1 block">{formatDateTime(attempt.at)}</Mono>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
