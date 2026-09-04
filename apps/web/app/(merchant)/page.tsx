"use client";

import {
  ArrowRight,
  CircleSlash,
  Coins,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  TrendingUp,
  Users,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ActionBadge, PolicyBadge, StatusBadge } from "@/components/status";
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
  LoadingState,
  Mono,
  SectionTitle,
  Spinner,
  TBody,
  THead,
  Table,
} from "@/components/ui/primitives";
import {
  api,
  type CaseSummary,
  type DashboardMetrics,
  type ScenarioRow,
} from "@/lib/api";
import { formatPercent, relativeTime } from "@/lib/utils";

function Metric({
  label,
  value,
  hint,
  tone = "neutral",
  icon: Icon,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
  icon?: React.ComponentType<{ className?: string }>;
}) {
  const toneClass = {
    neutral: "text-ink-100",
    good: "text-good-300",
    warn: "text-warn-300",
    bad: "text-bad-300",
    info: "text-accent-300",
  }[tone];
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="flex items-center justify-between">
          <p className="text-[11px] font-medium uppercase tracking-wider text-ink-500">
            {label}
          </p>
          {Icon ? <Icon className="h-3.5 w-3.5 text-ink-600" /> : null}
        </div>
        <p className={`tnum mt-2 text-2xl font-semibold tracking-tight ${toneClass}`}>
          {value}
        </p>
        {hint ? <p className="mt-1 text-[11px] text-ink-500">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

function CountTile({
  label,
  value,
  href,
  tone,
}: {
  label: string;
  value: number;
  href?: string;
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const body = (
    <div
      className="rounded-lg border border-ink-800 bg-ink-900/60 px-3 py-2.5
        transition-colors hover:border-ink-700"
    >
      <p className="tnum text-lg font-semibold text-ink-100">{value}</p>
      <p className="mt-0.5 text-[11px] leading-tight text-ink-500">{label}</p>
      {tone && value > 0 ? (
        <span
          className={`mt-1.5 block h-0.5 w-6 rounded ${
            { good: "bg-good-500", warn: "bg-warn-500", bad: "bg-bad-500", info: "bg-accent-500", neutral: "bg-ink-600" }[
              tone
            ]
          }`}
        />
      ) : null}
    </div>
  );
  return href ? <Link href={href}>{body}</Link> : body;
}

export default function DashboardPage() {
  const [metrics, setMetrics] = React.useState<DashboardMetrics | null>(null);
  const [cases, setCases] = React.useState<CaseSummary[]>([]);
  const [scenarios, setScenarios] = React.useState<ScenarioRow[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [flash, setFlash] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const [m, c, s] = await Promise.all([
        api.metrics(),
        api.cases({ limit: 8 }),
        api.scenarios(),
      ]);
      setMetrics(m);
      setCases(c);
      setScenarios(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function act(key: string, fn: () => Promise<unknown>, message: string) {
    setBusy(key);
    setFlash(null);
    try {
      const result = (await fn()) as Record<string, unknown>;
      const extra =
        typeof result?.processed === "number"
          ? ` — ${result.processed} processed, ${result.executed} executed, ` +
            `${result.blocked} blocked, ${result.awaiting_approval} held for approval`
          : typeof result?.mismatches_found === "number"
            ? ` — ${result.mismatches_found} mismatch(es) found`
            : "";
      setFlash(message + extra);
      await load();
    } catch (e) {
      setFlash(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  }

  if (loading) return <LoadingState label="Loading merchant dashboard" />;
  if (error) return <ErrorState detail={error} onRetry={() => void load()} />;
  if (!metrics) return <EmptyState title="No data" />;

  const chartData = metrics.trend.map((point) => ({
    label: point.label,
    "At risk": point.at_risk_paise / 100,
    Recovered: point.recovered_paise / 100,
  }));
  const hasTrend = chartData.some((d) => d["At risk"] > 0 || d.Recovered > 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink-100">
            Revenue recovery
          </h1>
          <p className="mt-1 text-xs text-ink-500">
            Northwind Commerce · {metrics.total_cases} cases tracked
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void load()}
            disabled={busy !== null}
          >
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              void act("seed", () => api.seedDemo(), "Demo data reset to its seeded state.")
            }
          >
            {busy === "seed" ? <Spinner className="h-3.5 w-3.5" /> : <RotateCcw className="h-3.5 w-3.5" />}
            Reset demo
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              void act("reconcile", () => api.reconcile(), "Reconciliation sweep complete.")
            }
          >
            {busy === "reconcile" ? <Spinner className="h-3.5 w-3.5" /> : <ShieldAlert className="h-3.5 w-3.5" />}
            Reconcile
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={busy !== null}
            onClick={() =>
              void act("batch", () => api.runBatch(200), "Recovery batch complete.")
            }
          >
            {busy === "batch" ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            Run recovery batch
          </Button>
        </div>
      </div>

      {flash ? (
        <div
          className="animate-in rounded-lg border border-accent-500/25 bg-accent-500/10
            px-4 py-2.5 text-xs text-accent-300"
        >
          {flash}
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Revenue at risk"
          value={metrics.revenue_at_risk.display}
          hint="Open cases where money is still uncollected"
          tone="warn"
          icon={ShieldAlert}
        />
        <Metric
          label="Recoverable"
          value={metrics.recoverable_revenue.display}
          hint="Predicted — positive expected value"
          tone="info"
          icon={TrendingUp}
        />
        <Metric
          label="Recovered"
          value={metrics.recovered_revenue.display}
          hint="Actual — only from verified payment events"
          tone="good"
          icon={Coins}
        />
        <Metric
          label="Net recovered"
          value={metrics.net_recovered_revenue.display}
          hint={`After ${metrics.intervention_cost.display} of intervention cost`}
          tone={metrics.net_recovered_revenue.paise >= 0 ? "good" : "bad"}
          icon={Coins}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Revenue at risk vs recovered</CardTitle>
            <CardDescription>
              Last 7 days, by the day the case opened. Recovery rate{" "}
              <span className="tnum text-ink-200">
                {formatPercent(metrics.recovery_rate)}
              </span>{" "}
              of all money the system has been responsible for.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {hasTrend ? (
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chartData} margin={{ top: 4, right: 4, left: -12, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="2 4" stroke="#222834" vertical={false} />
                    <XAxis
                      dataKey="label"
                      tick={{ fill: "#7b869c", fontSize: 11 }}
                      axisLine={{ stroke: "#222834" }}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fill: "#7b869c", fontSize: 11 }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={(v: number) => `₹${(v / 1000).toFixed(0)}k`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#12151c",
                        border: "1px solid #222834",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      labelStyle={{ color: "#cdd4e0" }}
                      formatter={(v: number) => `₹${v.toLocaleString("en-IN")}`}
                    />
                    <Bar dataKey="At risk" fill="#c98a1b" radius={[3, 3, 0, 0]} />
                    <Bar dataKey="Recovered" fill="#1fa971" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <EmptyState
                title="No activity in the last 7 days"
                detail="Reset the demo data, then run a recovery batch to populate the chart."
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Case outcomes</CardTitle>
            <CardDescription>Where every case currently sits.</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2">
            <CountTile label="Active" value={metrics.active_cases} tone="info" href="/cases" />
            <CountTile
              label="Recovered"
              value={metrics.successful_interventions}
              tone="good"
              href="/cases?status=RECOVERED"
            />
            <CountTile
              label="Stopped"
              value={metrics.stopped_cases}
              tone="neutral"
              href="/cases?status=STOPPED"
            />
            <CountTile
              label="Blocked"
              value={metrics.blocked_cases}
              tone="bad"
              href="/cases?status=BLOCKED"
            />
            <CountTile
              label="Awaiting approval"
              value={metrics.awaiting_approval}
              tone="warn"
              href="/cases?status=AWAITING_APPROVAL"
            />
            <CountTile
              label="Reconciliation"
              value={metrics.reconciliation_cases}
              tone="warn"
              href="/cases?status=RECONCILIATION"
            />
            <CountTile label="Escalated" value={metrics.escalated_cases} tone="warn" />
            <CountTile label="Customer contacts" value={metrics.customer_contacts} />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex flex-row items-center justify-between">
            <div>
              <CardTitle>Recent cases</CardTitle>
              <CardDescription>
                What the AI proposed, and what policy actually permitted.
              </CardDescription>
            </div>
            <Link
              href="/cases"
              className="flex items-center gap-1 text-xs text-accent-300 hover:text-accent-200"
            >
              All cases <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            {cases.length === 0 ? (
              <EmptyState title="No cases yet" detail="Reset the demo data to seed cases." />
            ) : (
              <Table>
                <THead>
                  <tr>
                    <th>Customer</th>
                    <th>Amount</th>
                    <th>AI proposed</th>
                    <th>Policy</th>
                    <th>Status</th>
                    <th>Opened</th>
                  </tr>
                </THead>
                <TBody>
                  {cases.map((row) => (
                    <tr key={row.id} className="hover:bg-ink-850/50">
                      <td>
                        <Link href={`/cases/${row.id}`} className="block">
                          <span className="text-ink-100 hover:text-accent-300">
                            {row.customer_name}
                          </span>
                          <Mono className="mt-0.5 block">{row.order_reference ?? row.id}</Mono>
                        </Link>
                      </td>
                      <td className="tnum text-ink-200">{row.amount.display}</td>
                      <td>
                        <ActionBadge action={row.ai_recommended_action} />
                      </td>
                      <td>
                        <PolicyBadge decision={row.policy_decision} />
                      </td>
                      <td>
                        <StatusBadge status={row.status} />
                      </td>
                      <td className="text-xs text-ink-500">{relativeTime(row.opened_at)}</td>
                    </tr>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Demo scenarios</CardTitle>
            <CardDescription>
              Seven seeded scenarios, including the ones where the system refuses to act.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {scenarios.map((scenario) => (
              <div
                key={scenario.scenario}
                className="rounded-lg border border-ink-800 bg-ink-850/40 px-3 py-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-xs font-medium text-ink-200">
                    <span className="text-ink-500">{scenario.scenario}.</span>{" "}
                    {scenario.title}
                  </p>
                  {scenario.case_status ? (
                    <Badge tone="neutral">{scenario.case_status}</Badge>
                  ) : null}
                </div>
                <p className="mt-1 text-[11px] leading-snug text-ink-500">
                  {scenario.detail}
                </p>
                {scenario.case_id ? (
                  <Link
                    href={`/cases/${scenario.case_id}`}
                    className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-accent-300
                      hover:text-accent-200"
                  >
                    Open case <ArrowRight className="h-3 w-3" />
                  </Link>
                ) : (
                  <p className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-ink-600">
                    <CircleSlash className="h-3 w-3" /> Run from the Audit screen
                  </p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      {metrics.open_exceptions > 0 ? (
        <Card className="border-warn-500/25 bg-warn-500/[0.04]">
          <CardContent className="flex items-center justify-between gap-4 pt-4">
            <div className="flex items-start gap-3">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn-400" />
              <div>
                <p className="text-sm font-medium text-warn-300">
                  {metrics.open_exceptions} unresolved exception
                  {metrics.open_exceptions === 1 ? "" : "s"}
                </p>
                <p className="mt-0.5 text-xs text-ink-400">
                  Cases the system could not confidently resolve. They are surfaced here
                  rather than quietly closed.
                </p>
              </div>
            </div>
            <Link href="/audit?tab=exceptions">
              <Button size="sm" variant="outline">
                Review <ArrowRight className="h-3 w-3" />
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : null}

      <SectionTitle hint="Predicted figures come from the deterministic scorer. Recovered revenue is only ever written from a signature-verified payment event.">
        How to read these numbers
      </SectionTitle>
    </div>
  );
}
