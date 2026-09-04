"use client";

import { RotateCcw, Save, ShieldCheck } from "lucide-react";
import * as React from "react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  Input,
  Label,
  LoadingState,
  Spinner,
  Toggle,
} from "@/components/ui/primitives";
import { api, type PolicyPayload } from "@/lib/api";
import { cn } from "@/lib/utils";

/** A rupee input bound to an integer-paise field.
 *  Money is stored in paise everywhere; only this component converts. */
function RupeeField({
  label,
  hint,
  paise,
  onChange,
}: {
  label: string;
  hint: string;
  paise: number;
  onChange: (paise: number) => void;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="text-sm text-ink-500">₹</span>
        <Input
          type="number"
          min={0}
          step={1}
          className="tnum"
          value={paise / 100}
          onChange={(e) => onChange(Math.round(Number(e.target.value || 0) * 100))}
        />
      </div>
      <p className="mt-1 text-[11px] text-ink-500">{hint}</p>
    </div>
  );
}

function NumberField({
  label,
  hint,
  value,
  onChange,
  min = 0,
  max = 1000,
  suffix,
}: {
  label: string;
  hint: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1.5 flex items-center gap-2">
        <Input
          type="number"
          min={min}
          max={max}
          className="tnum"
          value={value}
          onChange={(e) => onChange(Number(e.target.value || 0))}
        />
        {suffix ? <span className="shrink-0 text-xs text-ink-500">{suffix}</span> : null}
      </div>
      <p className="mt-1 text-[11px] text-ink-500">{hint}</p>
    </div>
  );
}

export default function PolicyPage() {
  const [policy, setPolicy] = React.useState<PolicyPayload | null>(null);
  const [original, setOriginal] = React.useState<PolicyPayload | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [flash, setFlash] = React.useState<{ text: string; bad?: boolean } | null>(null);

  const load = React.useCallback(async () => {
    setError(null);
    try {
      const data = await api.policy();
      setPolicy(data);
      setOriginal(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const dirty =
    policy !== null &&
    original !== null &&
    JSON.stringify(policy) !== JSON.stringify(original);

  async function save() {
    if (!policy) return;
    setSaving(true);
    setFlash(null);
    try {
      const saved = await api.savePolicy(policy);
      setPolicy(saved);
      setOriginal(saved);
      setFlash({
        text: "Policy saved. It applies to the next decision on every case.",
      });
    } catch (e) {
      setFlash({ text: (e as Error).message, bad: true });
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <LoadingState label="Loading merchant policy" />;
  if (error) return <ErrorState detail={error} onRetry={() => void load()} />;
  if (!policy) return null;

  const set = <K extends keyof PolicyPayload>(key: K, value: PolicyPayload[K]) =>
    setPolicy({ ...policy, [key]: value });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-ink-100">
            <ShieldCheck className="h-4 w-4 text-warn-400" /> Recovery policy
          </h1>
          <p className="mt-1 max-w-2xl text-xs text-ink-500">
            These limits are enforced in deterministic Python before any recovery action
            runs. The AI can propose whatever it likes; it cannot change or bypass a
            single value on this page.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!dirty || saving}
            onClick={() => setPolicy(original)}
          >
            <RotateCcw className="h-3.5 w-3.5" /> Discard
          </Button>
          <Button size="sm" variant="primary" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? <Spinner className="h-3.5 w-3.5" /> : <Save className="h-3.5 w-3.5" />}
            Save policy
          </Button>
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

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Automatic retries</CardTitle>
            <CardDescription>
              How hard the system is allowed to try before it must stop.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <NumberField
              label="Automatic retry limit"
              hint="Beyond this, RETRY_PAYMENT is blocked with RETRY_LIMIT_EXCEEDED."
              value={policy.max_auto_retries}
              onChange={(v) => set("max_auto_retries", v)}
              max={10}
            />
            <NumberField
              label="Minimum retry interval"
              hint="A retry sooner than this is blocked; the case waits instead."
              suffix="minutes"
              value={policy.min_retry_interval_minutes}
              onChange={(v) => set("min_retry_interval_minutes", v)}
              max={1440}
            />
            <NumberField
              label="Case expiry"
              hint="Cases older than this are closed rather than chased indefinitely."
              suffix="hours"
              value={policy.case_expiry_hours}
              onChange={(v) => set("case_expiry_hours", v)}
              min={1}
              max={8760}
            />
            <NumberField
              label="Customer contact limit"
              hint="Messages allowed per customer in a rolling 24 hours."
              suffix="/ 24h"
              value={policy.max_customer_contacts_24h}
              onChange={(v) => set("max_customer_contacts_24h", v)}
              max={20}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Amount limits</CardTitle>
            <CardDescription>
              Where automation stops and a human has to decide.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <RupeeField
              label="Maximum automatic recovery"
              hint="Above this, money-moving actions need merchant approval."
              paise={policy.max_auto_recovery_amount_paise}
              onChange={(v) => set("max_auto_recovery_amount_paise", v)}
            />
            <RupeeField
              label="Merchant approval threshold"
              hint="Must be at least the automatic limit, or the two rules contradict."
              paise={policy.approval_threshold_paise}
              onChange={(v) => set("approval_threshold_paise", v)}
            />
            <RupeeField
              label="Minimum expected value"
              hint="Cases whose expected value falls below this are not worth chasing."
              paise={policy.min_expected_value_paise}
              onChange={(v) => set("min_expected_value_paise", v)}
            />
            <div className="grid grid-cols-2 gap-3">
              <RupeeField
                label="Cost per contact"
                hint="Used in expected value."
                paise={policy.contact_cost_paise}
                onChange={(v) => set("contact_cost_paise", v)}
              />
              <RupeeField
                label="Cost per retry"
                hint="Used in expected value."
                paise={policy.retry_cost_paise}
                onChange={(v) => set("retry_cost_paise", v)}
              />
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Permitted recovery channels</CardTitle>
          <CardDescription>
            Turning one off makes the matching action return BLOCK on every case,
            immediately.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <Toggle
            checked={policy.allow_payment_link_recovery}
            onChange={(v) => set("allow_payment_link_recovery", v)}
            label="Allow payment-link recovery"
            hint="SEND_PAYMENT_LINK — a hosted link for the outstanding amount."
          />
          <Toggle
            checked={policy.allow_alternative_method}
            onChange={(v) => set("allow_alternative_method", v)}
            label="Allow alternative payment method"
            hint="OFFER_ALLOWED_ALTERNATIVE — the only route that works for an expired card."
          />
        </CardContent>
      </Card>

      <Card className="border-ink-800 bg-ink-900/40">
        <CardContent className="pt-4">
          <p className="text-xs leading-relaxed text-ink-500">
            <span className="text-ink-300">Rules that are not configurable.</span>{" "}
            Recovery is refused outright, on every merchant, when the order is already
            captured, refunded or cancelled; when a risk or fraud flag is present; when the
            failure class can never be fixed by a retry (expired card, revoked mandate,
            deliberate cancellation, risk decline); and when the event is a duplicate
            payment. Those are correctness constraints, not preferences, so they are not
            exposed as switches.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
