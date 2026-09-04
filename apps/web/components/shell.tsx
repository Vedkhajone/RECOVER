"use client";

import {
  Activity,
  AlertTriangle,
  BarChart3,
  FileClock,
  LayoutDashboard,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { Badge, Spinner } from "@/components/ui/primitives";
import { api, type SystemConfig } from "@/lib/api";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/cases", label: "Recovery cases", icon: Activity },
  { href: "/policy", label: "Policy", icon: ShieldCheck },
  { href: "/evaluation", label: "Evaluation", icon: BarChart3 },
  { href: "/audit", label: "Audit trail", icon: FileClock },
];

/** Header banner stating exactly what is wired up.
 *
 *  This is deliberately impossible to miss. A judge watching the demo should
 *  never have to ask whether they are looking at a real Razorpay transaction
 *  or a real model decision - the answer is on screen at all times. */
function ProvenanceBar({ config }: { config: SystemConfig | null }) {
  if (!config) {
    return (
      <div className="flex items-center gap-2 text-xs text-ink-500">
        <Spinner className="h-3 w-3" /> checking configuration
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={config.provider.is_razorpay ? "good" : "warn"} title={config.provider.note}>
        {config.provider.is_razorpay ? "Razorpay Test Mode" : "Payment simulator"}
      </Badge>
      <Badge tone={config.ai.enabled ? "info" : "neutral"} title={config.ai.note}>
        {config.ai.enabled ? `AI · ${config.ai.model}` : "Deterministic fallback"}
      </Badge>
      <Badge tone="neutral">{config.database}</Badge>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [config, setConfig] = React.useState<SystemConfig | null>(null);
  const [configError, setConfigError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .config()
      .then(setConfig)
      .catch((error: Error) => setConfigError(error.message));
  }, []);

  return (
    <div className="flex min-h-screen bg-ink-950">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-ink-850 bg-ink-900/50 lg:flex">
        <div className="border-b border-ink-850 px-5 py-5">
          <Link href="/" className="block">
            <span className="text-base font-semibold tracking-tight text-ink-100">
              RECOVER
            </span>
            <span className="mt-1 block text-[11px] leading-snug text-ink-500">
              Finds slipping revenue, recovers what it can, and knows when to stop.
            </span>
          </Link>
        </div>
        <nav className="flex-1 space-y-0.5 p-3">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-accent-500/12 text-accent-300"
                    : "text-ink-400 hover:bg-ink-850 hover:text-ink-200",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-ink-850 px-4 py-3">
          <p className="text-[10px] leading-relaxed text-ink-600">
            Prototype for the Razorpay Buildathon, using Razorpay Test Mode. Not an
            official Razorpay product.
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3
            border-b border-ink-850 bg-ink-950/85 px-5 py-3 backdrop-blur"
        >
          <div className="flex items-center gap-3 lg:hidden">
            <span className="text-sm font-semibold text-ink-100">RECOVER</span>
          </div>
          <nav className="flex gap-1 lg:hidden">
            {NAV.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className={cn(
                  "rounded px-2 py-1 text-[11px]",
                  pathname === href ? "bg-ink-800 text-ink-100" : "text-ink-500",
                )}
              >
                {label.split(" ")[0]}
              </Link>
            ))}
          </nav>
          <ProvenanceBar config={config} />
        </header>

        {configError ? (
          <div
            className="flex items-start gap-2 border-b border-bad-500/25 bg-bad-500/10
              px-5 py-2.5 text-xs text-bad-300"
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{configError}</span>
          </div>
        ) : null}

        <main className="min-w-0 flex-1 p-5">{children}</main>
      </div>
    </div>
  );
}
