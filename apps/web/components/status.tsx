/** Status vocabulary.
 *
 *  One place decides what colour a status is, so the same word never appears
 *  green on one screen and amber on another.
 */
import { Badge } from "@/components/ui/primitives";
import { humanise } from "@/lib/utils";

type Tone = "neutral" | "good" | "warn" | "bad" | "info";

const CASE_STATUS_TONE: Record<string, Tone> = {
  RECOVERED: "good",
  AWAITING_CUSTOMER: "info",
  AWAITING_APPROVAL: "warn",
  ESCALATED: "warn",
  RECONCILIATION: "warn",
  BLOCKED: "bad",
  STOPPED: "neutral",
  OPEN: "info",
  INVESTIGATING: "info",
  ACTION_PENDING: "info",
};

const POLICY_TONE: Record<string, Tone> = {
  ALLOW: "good",
  REQUIRE_APPROVAL: "warn",
  BLOCK: "bad",
  NOT_APPLICABLE: "neutral",
};

const ACTION_TONE: Record<string, Tone> = {
  RETRY_PAYMENT: "info",
  SEND_PAYMENT_LINK: "info",
  SEND_REMINDER: "info",
  OFFER_ALLOWED_ALTERNATIVE: "info",
  ESCALATE_TO_MERCHANT: "warn",
  WAIT: "neutral",
  STOP: "neutral",
};

const RECOVERABILITY_TONE: Record<string, Tone> = {
  high: "good",
  medium: "warn",
  low: "bad",
};

export function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-ink-600">—</span>;
  return <Badge tone={CASE_STATUS_TONE[status] ?? "neutral"}>{humanise(status)}</Badge>;
}

export function PolicyBadge({ decision }: { decision: string | null }) {
  if (!decision) return <span className="text-ink-600">—</span>;
  return <Badge tone={POLICY_TONE[decision] ?? "neutral"}>{humanise(decision)}</Badge>;
}

export function ActionBadge({ action }: { action: string | null }) {
  if (!action) return <span className="text-ink-600">—</span>;
  return <Badge tone={ACTION_TONE[action] ?? "neutral"}>{humanise(action)}</Badge>;
}

export function RecoverabilityBadge({ level }: { level: string | null }) {
  if (!level) return <span className="text-ink-600">—</span>;
  return <Badge tone={RECOVERABILITY_TONE[level] ?? "neutral"}>{level}</Badge>;
}

/** Says plainly whether a decision came from the model or the rule tree.
 *  This label is the difference between an honest demo and a dishonest one. */
export function AIPathBadge({ path, model }: { path: string | null; model?: string | null }) {
  if (!path) return null;
  if (path === "llm") {
    return <Badge tone="info">AI · {model ?? "model"}</Badge>;
  }
  if (path === "n/a") return <Badge tone="neutral">No diagnosis</Badge>;
  return <Badge tone="neutral">Deterministic fallback</Badge>;
}
