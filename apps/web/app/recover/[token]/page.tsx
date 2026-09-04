"use client";

/** Customer recovery page.
 *
 *  Deliberately not a dashboard. One order, one amount, one plain sentence
 *  about what went wrong, one button. No case internals, no AI reasoning, no
 *  policy codes, nothing about any other customer.
 */

import { AlertCircle, CheckCircle2, Lock, ShieldCheck } from "lucide-react";
import { useParams } from "next/navigation";
import Script from "next/script";
import * as React from "react";

import { Button, Spinner } from "@/components/ui/primitives";
import { api, type RecoveryPage } from "@/lib/api";

declare global {
  interface Window {
    Razorpay?: new (options: Record<string, unknown>) => { open: () => void };
  }
}

export default function CustomerRecoveryPage() {
  const params = useParams<{ token: string }>();
  const token = params.token;

  const [data, setData] = React.useState<RecoveryPage | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [paid, setPaid] = React.useState(false);
  const [failed, setFailed] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const page = await api.recoveryPage(token);
      setData(page);
      setPaid(page.paid);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** Simulator path: the customer chooses the outcome, exactly as Razorpay
   *  Test Mode lets you choose success or failure on a test card. */
  async function paySimulated(succeed: boolean) {
    setBusy(true);
    setFailed(null);
    try {
      const result = await api.simulatePayment(token, succeed);
      if (result.succeeded) {
        setPaid(true);
      } else {
        setFailed(
          "That attempt did not go through either. Nothing has been charged. " +
            "The merchant has been notified.",
        );
      }
      await load();
    } catch (e) {
      setFailed((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** Razorpay Test Mode path: open Checkout, then send the callback to the
   *  server for signature verification. The webhook, not this callback, is
   *  what actually records the recovery. */
  function payWithRazorpay() {
    if (!data?.razorpay_key_id || !data.razorpay_order_id) return;
    if (!window.Razorpay) {
      setFailed("Razorpay Checkout did not load. Check your connection and try again.");
      return;
    }
    setBusy(true);
    setFailed(null);
    const checkout = new window.Razorpay({
      key: data.razorpay_key_id,
      order_id: data.razorpay_order_id,
      amount: data.amount.paise,
      currency: "INR",
      name: "Northwind Commerce",
      description: data.description,
      prefill: { name: data.customer_name },
      theme: { color: "#4f7cff" },
      handler: async (response: Record<string, string>) => {
        try {
          await api.verifyCheckout(token, {
            razorpay_order_id: response.razorpay_order_id,
            razorpay_payment_id: response.razorpay_payment_id,
            razorpay_signature: response.razorpay_signature,
          });
          setPaid(true);
          await load();
        } catch (e) {
          setFailed((e as Error).message);
        } finally {
          setBusy(false);
        }
      },
      modal: { ondismiss: () => setBusy(false) },
    });
    checkout.open();
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-ink-950">
        <Spinner />
      </main>
    );
  }

  if (error || !data) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-ink-950 px-4">
        <div className="w-full max-w-md rounded-2xl border border-ink-800 bg-ink-900 p-8 text-center">
          <AlertCircle className="mx-auto h-8 w-8 text-bad-400" />
          <h1 className="mt-3 text-base font-semibold text-ink-100">
            This link is no longer valid
          </h1>
          <p className="mt-2 text-sm text-ink-400">
            {error ?? "The recovery link may have expired."} If you still want to complete
            this order, please contact the merchant.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-ink-950 px-4 py-10">
      {data.provider === "razorpay" ? (
        <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="afterInteractive" />
      ) : null}

      <div className="w-full max-w-md">
        <div className="mb-4 text-center">
          <span className="text-sm font-semibold tracking-tight text-ink-300">
            Northwind Commerce
          </span>
        </div>

        <div className="animate-in overflow-hidden rounded-2xl border border-ink-800 bg-ink-900 shadow-xl">
          {paid ? (
            <div className="px-8 py-10 text-center">
              <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-good-500/12">
                <CheckCircle2 className="h-7 w-7 text-good-400" />
              </div>
              <h1 className="mt-4 text-xl font-semibold text-ink-100">Payment successful</h1>
              <p className="mt-1.5 text-sm text-ink-400">Your order is confirmed.</p>
              <div className="mt-6 space-y-2 rounded-xl border border-ink-800 bg-ink-850/60 px-4 py-3 text-left">
                <div className="flex justify-between text-sm">
                  <span className="text-ink-500">Order</span>
                  <span className="text-ink-200">{data.order_reference}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-ink-500">Item</span>
                  <span className="text-ink-200">{data.description}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-ink-500">Paid</span>
                  <span className="tnum font-semibold text-good-300">
                    {data.amount.display}
                  </span>
                </div>
              </div>
              <p className="mt-5 text-[11px] leading-relaxed text-ink-600">
                A receipt has been recorded against your order. You can close this page.
              </p>
            </div>
          ) : (
            <>
              <div className="border-b border-ink-850 px-8 pt-8 pb-6 text-center">
                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-warn-500/12">
                  <AlertCircle className="h-6 w-6 text-warn-400" />
                </div>
                <h1 className="mt-4 text-lg font-semibold text-ink-100">
                  Payment couldn&rsquo;t be completed
                </h1>
                <p className="mt-2 text-sm leading-relaxed text-ink-400">
                  {data.reason_message} Nothing has been charged.
                </p>
              </div>

              <div className="px-8 py-6">
                <div className="space-y-2.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-ink-500">Order</span>
                    <span className="text-ink-200">{data.order_reference}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-ink-500">Item</span>
                    <span className="text-ink-200">{data.description}</span>
                  </div>
                  <div className="flex items-baseline justify-between border-t border-ink-850 pt-3">
                    <span className="text-sm text-ink-500">Amount due</span>
                    <span className="tnum text-2xl font-semibold text-ink-100">
                      {data.amount.display}
                    </span>
                  </div>
                </div>

                {failed ? (
                  <div className="mt-5 rounded-lg border border-bad-500/30 bg-bad-500/10 px-3 py-2.5 text-xs leading-relaxed text-bad-300">
                    {failed}
                  </div>
                ) : null}

                <div className="mt-6 space-y-2">
                  {data.provider === "razorpay" ? (
                    <Button
                      variant="primary"
                      size="lg"
                      className="w-full"
                      disabled={busy}
                      onClick={payWithRazorpay}
                    >
                      {busy ? <Spinner className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
                      Retry payment · {data.amount.display}
                    </Button>
                  ) : (
                    <>
                      <Button
                        variant="primary"
                        size="lg"
                        className="w-full"
                        disabled={busy}
                        onClick={() => void paySimulated(true)}
                      >
                        {busy ? <Spinner className="h-4 w-4" /> : <Lock className="h-4 w-4" />}
                        Retry payment · {data.amount.display}
                      </Button>
                      {/* Demo affordance. Razorpay Test Mode offers the same
                          choice via its success/failure test cards. */}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="w-full"
                        disabled={busy}
                        onClick={() => void paySimulated(false)}
                      >
                        Simulate a second failure
                      </Button>
                    </>
                  )}
                </div>

                <p className="mt-5 flex items-center justify-center gap-1.5 text-[11px] text-ink-600">
                  <ShieldCheck className="h-3 w-3" />
                  {data.provider === "razorpay"
                    ? "Secured by Razorpay Test Mode"
                    : "Local payment simulator — no real money moves"}
                </p>
              </div>
            </>
          )}
        </div>

        <p className="mt-4 text-center text-[10px] leading-relaxed text-ink-700">
          RECOVER — a prototype built for the Razorpay Buildathon using Razorpay Test Mode.
          Not an official Razorpay product.
        </p>
      </div>
    </main>
  );
}
