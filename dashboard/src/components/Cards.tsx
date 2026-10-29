// Reusable presentational primitives. All components are server-safe
// (no hooks or client-only APIs) so they can be rendered in app-router
// server components.

import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  /** Set when the card is itself a target — inside a Link, or carrying
   *  its own click. It then lifts under the pointer and gives way
   *  under the press; a card that does nothing must do neither, or
   *  the page promises an affordance it does not have. */
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  interactive?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl border border-council-200/80 dark:border-council-800 bg-white dark:bg-council-900 shadow-elev-1 p-4 sm:p-5 ${
        interactive
          ? "pressable-surface hover:shadow-elev-2 hover:border-council-300 dark:hover:border-council-700"
          : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

/** The caret beside a delta.
 *
 * It replaces ▲ / ▼, which are text: they picked up the font's own
 * weight and vertical metrics, sat a little low beside the figure,
 * and on the machines that substituted a colour emoji for them they
 * stopped matching the number they belonged to entirely. */
function Caret({ up }: { up: boolean }) {
  return (
    <svg
      viewBox="0 0 10 10"
      className="h-2.5 w-2.5 shrink-0"
      aria-hidden="true"
      fill="currentColor"
    >
      {up ? <path d="M5 1.5 9 8H1z" /> : <path d="M5 8.5 1 2h8z" />}
    </svg>
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
    <Card className={emphasis ? "ring-1 ring-gain/25" : ""}>
      <div className="eyebrow text-xs text-muted truncate">{label}</div>
      {/* The figure is the reason the card exists, so it gets the
          display size — and with it the tighter tracking the size
          scale now carries, which is what keeps a long NAV from
          reading as a row of separate digits.

          It has to shrink on a phone, though. Two of these cards sit
          side by side at 375px, which leaves about 123px of inner
          width, and "$128,355.20" set at text-3xl is twice that: the
          council's NAV was being cut off mid-number on the first
          screen of the site. Dropping a step at each breakpoint is
          what "scale the layout with the text" means here. */}
      <div className="text-xl sm:text-2xl lg:text-3xl font-semibold mt-1.5 tabular">
        {value}
      </div>
      {delta !== undefined && (
        <div className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm">
          {/* A tinted chip rather than coloured text: the sign of the
              day's move is the thing a reader looks for first, and a
              shape finds the eye faster than a hue does — which also
              means it survives for a reader who cannot separate the
              red from the green. */}
          <span
            className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-xs font-medium tabular ${
              delta >= 0
                ? "bg-gain/10 text-gain"
                : "bg-loss/10 text-loss"
            }`}
          >
            <Caret up={delta >= 0} />
            {Math.abs(delta).toFixed(2)}%
          </span>
          {deltaLabel ? (
            <span className="text-xs text-muted">{deltaLabel}</span>
          ) : null}
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
    <div className="mb-7">
      <h1 className="text-2xl sm:text-3xl font-semibold">{title}</h1>
      {subtitle && (
        <p className="mt-1.5 text-sm text-muted max-w-2xl">{subtitle}</p>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <Card className="text-center text-muted py-12">{children}</Card>
  );
}

export function PctCell({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="text-muted">—</span>;
  }
  const cls =
    value > 0 ? "text-gain" : value < 0 ? "text-loss" : "text-muted";
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
    return <span className="text-muted">—</span>;
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
    return <span className="text-muted">—</span>;
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
          : "text-muted";
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
