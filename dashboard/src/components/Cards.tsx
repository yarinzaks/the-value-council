// Reusable presentational primitives. All components are server-safe
// (no hooks or client-only APIs) so they can be rendered in app-router
// server components.

import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-council-200 dark:border-council-800 bg-white dark:bg-council-900 shadow-sm p-5 ${className}`}
    >
      {children}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  delta,
  deltaLabel,
  emphasis,
}: {
  label: string;
  value: string;
  /** Numeric delta — positive = green, negative = red. */
  delta?: number;
  deltaLabel?: string;
  emphasis?: boolean;
}) {
  return (
    <Card className={emphasis ? "ring-1 ring-gain/30" : ""}>
      <div className="text-xs uppercase tracking-wider text-council-500">{label}</div>
      <div className="text-3xl font-semibold mt-1 tabular">{value}</div>
      {delta !== undefined && (
        <div
          className={`text-sm mt-1 tabular ${
            delta >= 0 ? "text-gain" : "text-loss"
          }`}
        >
          {delta >= 0 ? "▲" : "▼"} {Math.abs(delta).toFixed(2)}%
          {deltaLabel ? <span className="text-council-500 ml-1">{deltaLabel}</span> : null}
        </div>
      )}
    </Card>
  );
}

export function PageTitle({
  title,
  subtitle,
}: {
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && (
        <p className="mt-1 text-sm text-council-500 dark:text-council-400">{subtitle}</p>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <Card className="text-center text-council-500 py-12">{children}</Card>
  );
}

export function PctCell({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="text-council-400">—</span>;
  }
  const cls =
    value > 0 ? "text-gain" : value < 0 ? "text-loss" : "text-council-500";
  return <span className={`tabular ${cls}`}>{value.toFixed(2)}%</span>;
}

export function NumCell({
  value,
  digits = 2,
}: {
  value: number | null | undefined;
  digits?: number;
}) {
  if (value === null || value === undefined || Number.isNaN(value))
    return <span className="text-council-400">—</span>;
  return <span className="tabular">{value.toFixed(digits)}</span>;
}

/** Currency formatter — always in USD, no fractional cents above $100. */
export function Money({
  value,
  signed = false,
  digits,
}: {
  value: number | null | undefined;
  /** Show explicit + sign for positive numbers (P&L style). */
  signed?: boolean;
  digits?: number;
}) {
  if (value === null || value === undefined || Number.isNaN(value))
    return <span className="text-council-400">—</span>;
  const abs = Math.abs(value);
  const decimals =
    digits !== undefined ? digits : abs < 100 ? 2 : 0;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  const cls =
    !signed
      ? ""
      : value > 0
        ? "text-gain"
        : value < 0
          ? "text-loss"
          : "text-council-500";
  const formatted = abs.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
  return (
    <span className={`tabular ${cls}`}>
      {signed && sign ? sign : value < 0 ? "−" : ""}${formatted}
    </span>
  );
}
