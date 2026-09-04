/** Typed client for the RECOVER API.
 *
 *  Every call goes to the FastAPI backend. The browser never talks to Razorpay
 *  or Anthropic directly and never holds a secret - the Razorpay key id is the
 *  only credential that reaches the client, and it is public by design.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Money = { paise: number; inr: number; display: string };

export type SystemConfig = {
  provider: {
    name: string;
    mode: string;
    is_razorpay: boolean;
    key_id_present: boolean;
    webhook_secret_configured: boolean;
    note: string;
  };
  ai: { enabled: boolean; model: string | null; path: string; note: string };
  database: string;
  merchant_id: string;
};

export type TrendPoint = {
  label: string;
  at_risk_paise: number;
  recovered_paise: number;
};

export type DashboardMetrics = {
  revenue_at_risk: Money;
  recoverable_revenue: Money;
  recovered_revenue: Money;
  net_recovered_revenue: Money;
  intervention_cost: Money;
  recovery_rate: number;
  active_cases: number;
  successful_interventions: number;
  stopped_cases: number;
  escalated_cases: number;
  blocked_cases: number;
  awaiting_approval: number;
  reconciliation_cases: number;
  customer_contacts: number;
  total_cases: number;
  open_exceptions: number;
  trend: TrendPoint[];
};

export type CaseSummary = {
  id: string;
  customer_name: string;
  customer_id: string;
  order_reference: string | null;
  description: string | null;
  amount: Money;
  event_type: string;
  status: string;
  root_cause: string | null;
  recoverability: string | null;
  ai_recommended_action: string | null;
  ai_path: string | null;
  policy_decision: string | null;
  policy_code: string | null;
  executed_action: string | null;
  recovered_amount: Money;
  recovery_probability: number;
  expected_value: Money;
  retry_count: number;
  contacts_sent: number;
  opened_at: string;
  closed_at: string | null;
};

export type PolicyMatrixEntry = {
  action: string;
  decision: string;
  code: string;
  reason: string;
  expected_value: Money;
};

export type DecisionPanel = {
  what_happened: string;
  why_it_happened: string | null;
  what_ai_recommends: string | null;
  why_ai_recommends_it: string | null;
  what_policy_allows: PolicyMatrixEntry[];
  what_action_was_taken: string | null;
  result: string;
  recovered_amount: Money;
  ai_path: string | null;
  ai_model: string | null;
  ai_confidence: number | null;
  ai_degraded: boolean;
  ai_tool_calls: string[];
  ai_validation_error: string | null;
  recovery_probability: number;
  analytics: Record<string, unknown> | null;
};

export type TimelineEntry = {
  actor: string;
  title: string;
  detail: string | null;
  at: string;
};

export type AttemptEntry = {
  action: string;
  policy_decision: string;
  succeeded: boolean | null;
  provider_reference: string | null;
  cost: Money;
  detail: string | null;
  at: string;
};

export type CaseDetail = CaseSummary & {
  customer_email: string;
  customer_segment: string;
  customer_success_rate: number;
  customer_successful_payments: number;
  customer_failed_payments: number;
  customer_risk_flagged: boolean;
  order_state: string | null;
  failure_reason: string | null;
  provider_error_code: string | null;
  provider_error_description: string | null;
  payment_link_url: string | null;
  recovery_token: string | null;
  decision_panel: DecisionPanel;
  timeline: TimelineEntry[];
  attempts: AttemptEntry[];
};

export type PolicyPayload = {
  max_auto_retries: number;
  min_retry_interval_minutes: number;
  max_auto_recovery_amount_paise: number;
  approval_threshold_paise: number;
  max_customer_contacts_24h: number;
  allow_payment_link_recovery: boolean;
  allow_alternative_method: boolean;
  case_expiry_hours: number;
  min_expected_value_paise: number;
  contact_cost_paise: number;
  retry_cost_paise: number;
};

export type AuditEntry = {
  id: number;
  at: string;
  actor: string;
  action: string;
  case_id: string | null;
  order_id: string | null;
  payment_id: string | null;
  tool: string | null;
  input_summary: string | null;
  result: string | null;
  policy_decision: string | null;
  reason: string | null;
  status: string;
  error: string | null;
};

export type WebhookEventRow = {
  event_id: string;
  event: string;
  signature_verified: boolean;
  delivery_count: number;
  duplicate: boolean;
  processed: boolean;
  effect: string | null;
  first_received_at: string;
  last_received_at: string;
};

export type ExceptionRow = {
  id: number;
  kind: string;
  detail: string;
  amount: Money;
  case_id: string | null;
  order_id: string | null;
  resolved: boolean;
  at: string;
};

export type RecoveryPage = {
  case_id: string;
  order_reference: string;
  description: string;
  amount: Money;
  customer_name: string;
  status: string;
  paid: boolean;
  reason_message: string;
  provider: string;
  provider_mode: string;
  razorpay_key_id: string | null;
  razorpay_order_id: string | null;
  payment_link_url: string | null;
};

export type PolicyProbe = {
  action: string;
  decision: string;
  code: string;
  reason: string;
  expected_value: Money;
  recovery_probability: number;
  would_execute: boolean;
};

export type ScenarioRow = {
  scenario: string;
  title: string;
  case_id: string | null;
  detail: string;
  case_status: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      `Cannot reach the RECOVER API at ${API_BASE}. Is the backend running?`,
      0,
    );
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      /* keep the status text */
    }
    throw new ApiError(String(detail), response.status);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : "{}" });

export const api = {
  config: () => request<SystemConfig>("/api/demo/config"),
  scenarios: () => request<ScenarioRow[]>("/api/demo/scenarios"),
  metrics: () => request<DashboardMetrics>("/api/dashboard/metrics"),

  cases: (params: Record<string, string | number | undefined> = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString() ? `?${query}` : "";
    return request<CaseSummary[]>(`/api/cases${suffix}`);
  },
  case: (id: string) => request<CaseDetail>(`/api/cases/${id}`),
  processCase: (id: string, deterministic = false) =>
    post<Record<string, unknown>>(
      `/api/cases/${id}/process?use_deterministic_engine=${deterministic}`,
    ),
  approveCase: (id: string) => post<Record<string, unknown>>(`/api/cases/${id}/approve`),
  rejectCase: (id: string) => post<Record<string, unknown>>(`/api/cases/${id}/reject`),
  probePolicy: (id: string, action: string) =>
    post<PolicyProbe>(`/api/cases/${id}/policy-probe`, { action }),
  runBatch: (limit = 100, deterministic = false) =>
    post<Record<string, unknown>>("/api/cases/batch/run", {
      limit,
      use_deterministic_engine: deterministic,
    }),
  reconcile: () => post<Record<string, unknown>>("/api/demo/reconcile"),

  policy: () => request<PolicyPayload>("/api/policy"),
  savePolicy: (payload: PolicyPayload) =>
    request<PolicyPayload>("/api/policy", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  audit: (params: Record<string, string | number | undefined> = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    const suffix = query.toString() ? `?${query}` : "";
    return request<AuditEntry[]>(`/api/audit${suffix}`);
  },
  webhookEvents: () => request<WebhookEventRow[]>("/api/webhooks/events"),
  exceptions: () => request<ExceptionRow[]>("/api/exceptions"),
  replayWebhook: (eventId: string) =>
    post<Record<string, unknown>>("/api/demo/replay-webhook", { event_id: eventId }),

  evaluation: () => request<Record<string, any>>("/api/evaluation/latest"),
  runEvaluation: () => post<Record<string, unknown>>("/api/evaluation/run"),

  seedDemo: () => post<Record<string, unknown>>("/api/demo/seed"),

  recoveryPage: (token: string) => request<RecoveryPage>(`/api/recovery/${token}`),
  simulatePayment: (token: string, succeed: boolean, failureReason = "BANK_TRANSIENT") =>
    post<Record<string, any>>(`/api/recovery/${token}/simulate-payment`, {
      succeed,
      failure_reason: failureReason,
    }),
  verifyCheckout: (
    token: string,
    payload: {
      razorpay_order_id: string;
      razorpay_payment_id: string;
      razorpay_signature: string;
    },
  ) => post<Record<string, unknown>>(`/api/recovery/${token}/verify`, payload),
};
