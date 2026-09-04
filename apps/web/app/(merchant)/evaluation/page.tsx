"use client";

import { BarChart3, Play, RefreshCw } from "lucide-react";
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
  LoadingState,
  Mono,
  Spinner,
  TBody,
  THead,
  Table,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { cn, formatDateTime, humanise } from "@/lib/utils";

type Block = Record<string, any>;

function StatRow({
  label,
  dev,
  holdout,
  format = (v: number) => v.toFixed(4),
  emphasise,
}: {
  label: string;
  dev: number;
  holdout: number;
  format?: (v: number) => string;
  emphasise?: boolean;
}) {
  return (
    <tr className="hover:bg-ink-850/40">
      <td className={cn("text-xs", emphasise ? "text-ink-100" : "text-ink-300")}>{label}</td>
      <td className="tnum text-right text-xs text-ink-500">{format(dev)}</td>
      <td
        className={cn(
          "tnum text-right text-xs",
          emphasise ? "font-semibold text-ink-100" : "text-ink-200",
        )}
      >
        {format(holdout)}
      </td>
    </tr>
  );
}

const money = (v: number) =>
  `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const count = (v: number) => v.toLocaleString("en-IN");
const ratio = (v: number) => v.toFixed(4);

export default function EvaluationPage() {
  const [data, setData] = React.useState<Block | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      setData(await api.evaluation());
    } catch (e) {
      setError((e as Error).message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  async function run() {
    setBusy(true);
    try {
      await api.runEvaluation();
      // The run is queued in the background; poll briefly for the new row.
      await new Promise((resolve) => setTimeout(resolve, 4000));
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <LoadingState label="Loading evaluation" />;

  if (error || !data) {
    return (
      <div className="space-y-4">
        <h1 className="text-lg font-semibold tracking-tight text-ink-100">Evaluation</h1>
        <Card>
          <CardContent className="pt-4">
            <EmptyState
              title="No evaluation has been run yet"
              detail={
                error ??
                "Run the evaluation to populate this screen. Nothing on this page is hardcoded — it reads a stored run."
              }
              action={
                <div className="flex flex-col items-center gap-3">
                  <Button variant="primary" size="sm" disabled={busy} onClick={() => void run()}>
                    {busy ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                    Run evaluation now
                  </Button>
                  <Mono>python scripts/run_evaluation.py</Mono>
                </div>
              }
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  const dev: Block = data.development;
  const hold: Block = data.holdout;
  const exceptions: Block[] = data.holdout_exceptions ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-ink-100">
            <BarChart3 className="h-4 w-4 text-accent-400" /> Evaluation
          </h1>
          <p className="mt-1 max-w-3xl text-xs text-ink-500">
            The shipped decision path — same detector, same agent, same policy engine — run
            over synthetic events and scored against ground truth it never saw. Thresholds
            are fitted on the development split only.
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" /> Refresh
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run()}>
            {busy ? <Spinner className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            Re-run
          </Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[
          { label: "Dataset", value: count(data.dataset_size), hint: "synthetic events" },
          { label: "Development", value: count(data.dev_size), hint: "used for tuning" },
          { label: "Held out", value: count(data.holdout_size), hint: "never tuned on" },
          {
            label: "Decision engine",
            value: data.decision_engine === "llm" ? "LLM" : "Deterministic",
            hint: data.decision_engine,
          },
          { label: "Seed", value: String(data.dataset_seed), hint: "reproducible" },
        ].map((tile) => (
          <Card key={tile.label}>
            <CardContent className="pt-4">
              <p className="text-[10px] uppercase tracking-wider text-ink-500">
                {tile.label}
              </p>
              <p className="tnum mt-1.5 text-xl font-semibold text-ink-100">{tile.value}</p>
              <p className="mt-0.5 text-[11px] text-ink-500">{tile.hint}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card className="border-accent-500/20 bg-accent-500/[0.04]">
        <CardContent className="pt-4">
          <p className="text-xs leading-relaxed text-ink-400">
            <span className="font-medium text-accent-300">Predicted vs actual.</span>{" "}
            &ldquo;Detection&rdquo; and &ldquo;eligibility&rdquo; describe what RECOVER
            decided from observable evidence. &ldquo;Recovered revenue&rdquo; is an actual
            figure: it is credited only when the system took an action that ground truth
            says would genuinely have collected the money, on a case ground truth says was
            genuinely recoverable, within the number of attempts that would ever have
            worked. The held-out column is the honest one.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Decision quality</CardTitle>
            <CardDescription>Development vs held-out.</CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            <Table>
              <THead>
                <tr>
                  <th>Metric</th>
                  <th className="!text-right">Development</th>
                  <th className="!text-right">Held out</th>
                </tr>
              </THead>
              <TBody>
                <StatRow
                  label="Revenue-at-risk detection precision"
                  dev={dev.detection.precision}
                  holdout={hold.detection.precision}
                  emphasise
                />
                <StatRow
                  label="Revenue-at-risk detection recall"
                  dev={dev.detection.recall}
                  holdout={hold.detection.recall}
                  emphasise
                />
                <StatRow
                  label="Recovery eligibility precision"
                  dev={dev.recovery_eligibility.precision}
                  holdout={hold.recovery_eligibility.precision}
                />
                <StatRow
                  label="Recovery eligibility recall"
                  dev={dev.recovery_eligibility.recall}
                  holdout={hold.recovery_eligibility.recall}
                />
                <StatRow
                  label="Action-selection accuracy (all)"
                  dev={dev.action_selection.accuracy_all_records}
                  holdout={hold.action_selection.accuracy_all_records}
                />
                <StatRow
                  label="Action-selection accuracy (detected)"
                  dev={dev.action_selection.accuracy_detected_only}
                  holdout={hold.action_selection.accuracy_detected_only}
                  emphasise
                />
                <StatRow
                  label="Recovery rate"
                  dev={dev.recovery_rate}
                  holdout={hold.recovery_rate}
                  format={ratio}
                  emphasise
                />
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Money</CardTitle>
            <CardDescription>
              Simulated against ground truth, not collected from a bank.
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0 pb-0">
            <Table>
              <THead>
                <tr>
                  <th>Metric</th>
                  <th className="!text-right">Development</th>
                  <th className="!text-right">Held out</th>
                </tr>
              </THead>
              <TBody>
                <StatRow
                  label="Total revenue at risk"
                  dev={dev.money_inr.total_revenue_at_risk}
                  holdout={hold.money_inr.total_revenue_at_risk}
                  format={money}
                />
                <StatRow
                  label="Revenue eligible for recovery"
                  dev={dev.money_inr.revenue_eligible_for_recovery}
                  holdout={hold.money_inr.revenue_eligible_for_recovery}
                  format={money}
                />
                <StatRow
                  label="Revenue actually recovered"
                  dev={dev.money_inr.revenue_actually_recovered}
                  holdout={hold.money_inr.revenue_actually_recovered}
                  format={money}
                  emphasise
                />
                <StatRow
                  label="Intervention cost"
                  dev={dev.money_inr.intervention_cost}
                  holdout={hold.money_inr.intervention_cost}
                  format={money}
                />
                <StatRow
                  label="False-positive intervention cost"
                  dev={dev.money_inr.false_positive_intervention_cost}
                  holdout={hold.money_inr.false_positive_intervention_cost}
                  format={money}
                />
                <StatRow
                  label="Net recovered revenue"
                  dev={dev.money_inr.net_recovered_revenue}
                  holdout={hold.money_inr.net_recovered_revenue}
                  format={money}
                  emphasise
                />
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Safety and restraint</CardTitle>
          <CardDescription>
            What the system chose not to do, and what it got wrong when it did act.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          <Table>
            <THead>
              <tr>
                <th>Metric</th>
                <th className="!text-right">Development</th>
                <th className="!text-right">Held out</th>
              </tr>
            </THead>
            <TBody>
              <StatRow
                label="Interventions made"
                dev={dev.safety.interventions}
                holdout={hold.safety.interventions}
                format={count}
              />
              <StatRow
                label="False-positive interventions"
                dev={dev.safety.false_positive_interventions}
                holdout={hold.safety.false_positive_interventions}
                format={count}
                emphasise
              />
              <StatRow
                label="Unnecessary customer contacts"
                dev={dev.safety.unnecessary_customer_contacts}
                holdout={hold.safety.unnecessary_customer_contacts}
                format={count}
                emphasise
              />
              <StatRow
                label="Total customer contacts"
                dev={dev.safety.total_customer_contacts}
                holdout={hold.safety.total_customer_contacts}
                format={count}
              />
              <StatRow
                label="Unsafe actions blocked by policy"
                dev={dev.safety.blocked_unsafe_actions}
                holdout={hold.safety.blocked_unsafe_actions}
                format={count}
                emphasise
              />
              <StatRow
                label="Policy refusals (all)"
                dev={dev.safety.policy_refusals}
                holdout={hold.safety.policy_refusals}
                format={count}
              />
              <StatRow
                label="Cases stopped"
                dev={dev.safety.cases_stopped}
                holdout={hold.safety.cases_stopped}
                format={count}
              />
              <StatRow
                label="Cases escalated"
                dev={dev.safety.cases_escalated}
                holdout={hold.safety.cases_escalated}
                format={count}
              />
              <StatRow
                label="Cases awaiting merchant approval"
                dev={dev.safety.cases_awaiting_approval}
                holdout={hold.safety.cases_awaiting_approval}
                format={count}
              />
              <StatRow
                label="Unresolved exceptions"
                dev={dev.safety.unresolved_exceptions}
                holdout={hold.safety.unresolved_exceptions}
                format={count}
                emphasise
              />
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Exceptions — held-out cases the system could not confidently resolve</CardTitle>
          <CardDescription>
            Largest first. These are shown, not hidden: they are where the system disagrees
            with ground truth or hands the decision to a human.
          </CardDescription>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {exceptions.length === 0 ? (
            <EmptyState title="No exceptions in this run" />
          ) : (
            <Table>
              <THead>
                <tr>
                  <th>Transaction</th>
                  <th>Amount</th>
                  <th>Detected</th>
                  <th>Proposed</th>
                  <th>Ground truth</th>
                  <th>Policy</th>
                  <th>Issue</th>
                </tr>
              </THead>
              <TBody>
                {exceptions.map((row) => (
                  <tr key={String(row.transaction_id)} className="hover:bg-ink-850/40">
                    <td>
                      <Mono>{String(row.transaction_id)}</Mono>
                    </td>
                    <td className="tnum whitespace-nowrap text-ink-200">
                      ₹{Number(row.amount_inr).toLocaleString("en-IN")}
                    </td>
                    <td>
                      <Badge tone={row.detected ? "info" : "neutral"}>
                        {row.detected ? "Yes" : "No"}
                      </Badge>
                    </td>
                    <td className="text-xs text-ink-300">
                      {humanise(String(row.proposed_action))}
                    </td>
                    <td className="text-xs text-ink-300">
                      {humanise(String(row.ground_truth_best_action))}
                    </td>
                    <td>
                      <Badge
                        tone={
                          row.policy_decision === "ALLOW"
                            ? "good"
                            : row.policy_decision === "BLOCK"
                              ? "bad"
                              : "warn"
                        }
                      >
                        {humanise(String(row.policy_decision))}
                      </Badge>
                    </td>
                    <td className="max-w-[280px]">
                      <Mono className="text-warn-300">{String(row.issue)}</Mono>
                    </td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <p className="text-[11px] leading-relaxed text-ink-600">
        Run {data.id} · {formatDateTime(data.created_at)} · computed in{" "}
        {String(data.elapsed_seconds)}s. {String(data.note ?? "")}
      </p>
    </div>
  );
}
