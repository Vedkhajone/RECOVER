/** Core UI primitives.
 *
 *  Written in the shadcn/ui style - unstyled Radix-free elements plus a `cva`
 *  variant table, copied into the repo rather than pulled from a component
 *  library. The shadcn CLI needs an interactive init, so these are authored
 *  here directly; the pattern (own your components, style with Tailwind
 *  tokens) is the same.
 */
"use client";

import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Card */

export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "rounded-xl border border-ink-800 bg-ink-900/70 shadow-sm backdrop-blur",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 pt-4 pb-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      className={cn("text-sm font-semibold tracking-tight text-ink-100", className)}
      {...props}
    />
  );
}

export function CardDescription({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("mt-1 text-xs text-ink-400", className)} {...props} />;
}

export function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 pb-5", className)} {...props} />;
}

/* -------------------------------------------------------------- Button */

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium " +
    "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
    "focus-visible:ring-accent-500/60 disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        primary: "bg-accent-500 text-white hover:bg-accent-400",
        secondary: "bg-ink-800 text-ink-100 hover:bg-ink-700 border border-ink-700",
        ghost: "text-ink-300 hover:bg-ink-800 hover:text-ink-100",
        good: "bg-good-500 text-white hover:bg-good-400",
        danger: "bg-bad-500 text-white hover:bg-bad-400",
        outline: "border border-ink-700 text-ink-200 hover:bg-ink-800",
      },
      size: {
        sm: "h-8 px-3 text-xs",
        md: "h-9 px-4",
        lg: "h-11 px-6 text-base",
        icon: "h-8 w-8",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return (
    <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
  );
}

/* --------------------------------------------------------------- Badge */

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] " +
    "font-medium tracking-wide whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-ink-700 bg-ink-800 text-ink-300",
        good: "border-good-500/35 bg-good-500/12 text-good-300",
        warn: "border-warn-500/35 bg-warn-500/12 text-warn-300",
        bad: "border-bad-500/35 bg-bad-500/12 text-bad-300",
        info: "border-accent-500/35 bg-accent-500/12 text-accent-300",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export interface BadgeProps
  extends React.ComponentProps<"span">,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}

/* --------------------------------------------------------------- Table */

export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}

export function THead({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      className={cn(
        "text-[11px] uppercase tracking-wider text-ink-500 [&_th]:px-4 [&_th]:py-2.5 " +
          "[&_th]:text-left [&_th]:font-medium",
        className,
      )}
      {...props}
    />
  );
}

export function TBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      className={cn(
        "[&_td]:px-4 [&_td]:py-3 [&_tr]:border-t [&_tr]:border-ink-800",
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------------------------------------- Form */

export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-lg border border-ink-700 bg-ink-850 px-3 text-sm " +
          "text-ink-100 placeholder:text-ink-500 focus:border-accent-500 " +
          "focus:outline-none focus:ring-1 focus:ring-accent-500/50",
        className,
      )}
      {...props}
    />
  );
}

export function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-9 w-full rounded-lg border border-ink-700 bg-ink-850 px-2.5 text-sm " +
          "text-ink-100 focus:border-accent-500 focus:outline-none",
        className,
      )}
      {...props}
    />
  );
}

export function Label({ className, ...props }: React.ComponentProps<"label">) {
  return (
    <label
      className={cn("block text-xs font-medium text-ink-300", className)}
      {...props}
    />
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-start justify-between gap-4 rounded-lg border
        border-ink-800 bg-ink-850/60 px-3 py-2.5 text-left transition-colors
        hover:border-ink-700"
    >
      <span>
        <span className="block text-xs font-medium text-ink-200">{label}</span>
        {hint ? <span className="mt-0.5 block text-[11px] text-ink-500">{hint}</span> : null}
      </span>
      <span
        className={cn(
          "mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors",
          checked ? "bg-good-500" : "bg-ink-700",
        )}
      >
        <span
          className={cn(
            "block h-4 w-4 rounded-full bg-white transition-transform",
            checked && "translate-x-4",
          )}
        />
      </span>
    </button>
  );
}

/* --------------------------------------------------------------- State */

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-4 w-4 animate-spin rounded-full border-2",
        "border-ink-600 border-t-accent-400",
        className,
      )}
    />
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-ink-400">
      <Spinner />
      {label}…
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <p className="text-sm font-medium text-ink-200">{title}</p>
      {detail ? <p className="max-w-md text-xs text-ink-500">{detail}</p> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  detail,
  onRetry,
}: {
  title?: string;
  detail?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className="rounded-lg border border-bad-500/30 bg-bad-500/10 px-4 py-3">
        <p className="text-sm font-medium text-bad-300">{title}</p>
        {detail ? <p className="mt-1 max-w-lg text-xs text-bad-300/80">{detail}</p> : null}
      </div>
      {onRetry ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/* ---------------------------------------------------------------- Misc */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse-slow rounded bg-ink-800", className)} />;
}

export function Mono({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      className={cn("font-mono text-[11px] tracking-tight text-ink-400", className)}
      {...props}
    />
  );
}

export function SectionTitle({
  children,
  hint,
}: {
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="mb-3">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-400">
        {children}
      </h2>
      {hint ? <p className="mt-1 text-xs text-ink-500">{hint}</p> : null}
    </div>
  );
}
