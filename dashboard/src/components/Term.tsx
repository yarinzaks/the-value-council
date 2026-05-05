"use client";

import { useUI } from "./Providers";
import { GLOSSARY, getTerm } from "@/lib/glossary";

/**
 * Wrap a financial term to add a hover tooltip explaining what it
 * means. Usage:
 *
 *   <Term k="cagr">CAGR</Term>
 *   <Term k="pe" />   // shows the canonical term name
 *
 * Hover or focus reveals a small popover with the language-aware
 * definition. Clicking opens it on touch devices via :focus-within.
 */
export function Term({
  k,
  children,
  className = "",
}: {
  k: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const { locale } = useUI();
  const def = getTerm(k);
  if (!def) {
    return <span className={className}>{children}</span>;
  }
  const text = locale === "he" ? def.he : def.en;
  const termLabel = locale === "he" ? def.term_he ?? def.term : def.term;
  return (
    <span className={`relative group inline-block cursor-help ${className}`} tabIndex={0}>
      <span className="border-b border-dotted border-council-400 dark:border-council-500">
        {children ?? termLabel}
      </span>
      <span
        role="tooltip"
        className="invisible group-hover:visible group-focus:visible group-focus-within:visible
                   absolute z-50 left-1/2 -translate-x-1/2 mt-1 top-full
                   w-64 p-3 rounded-lg shadow-lg
                   bg-council-900 dark:bg-council-100
                   text-council-50 dark:text-council-900
                   text-xs leading-snug normal-case font-normal"
      >
        <strong className="block text-[11px] uppercase tracking-wider mb-1 opacity-70">
          {termLabel}
        </strong>
        {text}
      </span>
    </span>
  );
}

/** Render a list of all terms — useful for a dedicated glossary page. */
export function GlossaryTable() {
  const { locale } = useUI();
  const entries = Object.entries(GLOSSARY);
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left border-b border-council-200 dark:border-council-800">
          <th className="py-2 pr-3 font-medium">Term</th>
          <th className="py-2 pl-3 font-medium">Meaning</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, def]) => (
          <tr
            key={key}
            className="border-b border-council-100 dark:border-council-800 last:border-b-0"
          >
            <td className="py-2 pr-3 font-medium align-top whitespace-nowrap">
              {locale === "he" ? def.term_he ?? def.term : def.term}
            </td>
            <td className="py-2 pl-3 text-council-700 dark:text-council-300">
              {locale === "he" ? def.he : def.en}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
