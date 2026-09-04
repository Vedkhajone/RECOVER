"use client";

import { Search, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { ActionBadge, AIPathBadge, PolicyBadge, StatusBadge } from "@/components/status";
import {
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  LoadingState,
  Mono,
  Select,
  TBody,
  THead,
  Table,
} from "@/components/ui/primitives";
import { api, type CaseSummary } from "@/lib/api";
import { formatPercent, relativeTime } from "@/lib/utils";

const STATUSES = [
  "OPEN",
  "INVESTIGATING",
  "AWAITING_CUSTOMER",
  "AWAITING_APPROVAL",
  "RECOVERED",
  "STOPPED",
  "BLOCKED",
  "ESCALATED",
  "RECONCILIATION",
];

const EVENT_TYPES = [
  "PAYMENT_FAILURE",
  "CHECKOUT_ABANDONMENT",
  "SUBSCRIPTION_PAYMENT_FAILURE",
  "OVERDUE_INVOICE",
  "STATE_MISMATCH",
  "DUPLICATE_PAYMENT",
];

function CasesTable() {
  const router = useRouter();
  const params = useSearchParams();
  const status = params.get("status") ?? "";
  const eventType = params.get("event_type") ?? "";

  const [search, setSearch] = React.useState(params.get("search") ?? "");
  const [rows, setRows] = React.useState<CaseSummary[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRows(
        await api.cases({
          status: status || undefined,
          event_type: eventType || undefined,
          search: search || undefined,
          limit: 200,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [status, eventType, search]);

  React.useEffect(() => {
    const timer = setTimeout(() => void load(), 200);
    return () => clearTimeout(timer);
  }, [load]);

  function setParam(key: string, value: string) {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`/cases?${next.toString()}`);
  }

  const totalAtRisk = rows.reduce((sum, r) => sum + r.amount.paise, 0);
  const totalRecovered = rows.reduce((sum, r) => sum + r.recovered_amount.paise, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink-100">
            AI recovery cases
          </h1>
          <p className="mt-1 text-xs text-ink-500">
            {rows.length} case{rows.length === 1 ? "" : "s"} · ₹
            {(totalAtRisk / 100).toLocaleString("en-IN")} at risk · ₹
            {(totalRecovered / 100).toLocaleString("en-IN")} recovered
          </p>
        </div>
      </div>

      <Card>
        <CardContent className="pt-4">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[220px] flex-1">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-ink-500" />
                <Input
                  className="pl-8"
                  placeholder="Customer, order reference or case id"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
            <div className="w-48">
              <Select value={status} onChange={(e) => setParam("status", e.target.value)}>
                <option value="">All statuses</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </Select>
            </div>
            <div className="w-56">
              <Select
                value={eventType}
                onChange={(e) => setParam("event_type", e.target.value)}
              >
                <option value="">All event types</option>
                {EVENT_TYPES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace(/_/g, " ")}
                  </option>
                ))}
              </Select>
            </div>
            {status || eventType || search ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  router.replace("/cases");
                }}
              >
                <SlidersHorizontal className="h-3.5 w-3.5" /> Clear
              </Button>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="px-0 pb-0 pt-0">
          {loading ? (
            <LoadingState label="Loading cases" />
          ) : error ? (
            <ErrorState detail={error} onRetry={() => void load()} />
          ) : rows.length === 0 ? (
            <EmptyState
              title="No cases match these filters"
              detail="Clear the filters, or reset the demo data from the dashboard."
            />
          ) : (
            <Table>
              <THead>
                <tr>
                  <th>Case</th>
                  <th>Problem</th>
                  <th>Amount</th>
                  <th>P(recover)</th>
                  <th>AI proposed</th>
                  <th>Policy</th>
                  <th>Action taken</th>
                  <th>Status</th>
                  <th>Recovered</th>
                  <th>Opened</th>
                </tr>
              </THead>
              <TBody>
                {rows.map((row) => (
                  <tr key={row.id} className="hover:bg-ink-850/50">
                    <td>
                      <Link href={`/cases/${row.id}`} className="block">
                        <span className="font-medium text-ink-100 hover:text-accent-300">
                          {row.customer_name}
                        </span>
                        <Mono className="mt-0.5 block">
                          {row.order_reference ?? row.id}
                        </Mono>
                      </Link>
                    </td>
                    <td className="max-w-[190px]">
                      <span className="block truncate text-xs text-ink-300">
                        {row.event_type.replace(/_/g, " ").toLowerCase()}
                      </span>
                      <span className="mt-0.5 block truncate text-[11px] text-ink-500">
                        {row.root_cause ?? "Not yet diagnosed"}
                      </span>
                    </td>
                    <td className="tnum whitespace-nowrap text-ink-200">
                      {row.amount.display}
                    </td>
                    <td className="tnum text-xs text-ink-400">
                      {formatPercent(row.recovery_probability, 0)}
                    </td>
                    <td>
                      <div className="flex flex-col gap-1">
                        <ActionBadge action={row.ai_recommended_action} />
                        <AIPathBadge path={row.ai_path} />
                      </div>
                    </td>
                    <td>
                      <PolicyBadge decision={row.policy_decision} />
                      {row.policy_code ? (
                        <Mono className="mt-1 block">{row.policy_code}</Mono>
                      ) : null}
                    </td>
                    <td>
                      <ActionBadge action={row.executed_action} />
                    </td>
                    <td>
                      <StatusBadge status={row.status} />
                    </td>
                    <td className="tnum whitespace-nowrap">
                      {row.recovered_amount.paise > 0 ? (
                        <span className="text-good-300">{row.recovered_amount.display}</span>
                      ) : (
                        <span className="text-ink-600">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap text-xs text-ink-500">
                      {relativeTime(row.opened_at)}
                    </td>
                  </tr>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function CasesPage() {
  return (
    <React.Suspense fallback={<LoadingState label="Loading cases" />}>
      <CasesTable />
    </React.Suspense>
  );
}
