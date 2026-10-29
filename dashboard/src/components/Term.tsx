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
      {/* The definition grows out of the word it defines.
       *
       * It used to appear at full size, already there, which gives the
       * reader nothing to connect it to — a panel that arrives from
       * nowhere has to be found before it can be read. Scaling it up
       * from its own top edge, anchored under the term, makes the
       * relationship between the two the first thing visible. The
       * reduced-motion rule in globals.css collapses this to a plain
       * appearance, which is the right fallback: the information is
       * the point, the growth is only how it got here. */}
      <span
        role="tooltip"
        className="pointer-events-none opacity-0 scale-95
                   group-hover:opacity-100 group-hover:scale-100
                   group-focus:opacity-100 group-focus:scale-100
                   group-focus-within:opacity-100 group-focus-within:scale-100
                   origin-top transition-[opacity,transform] duration-quick ease-standard
                   absolute z-50 left-1/2 -translate-x-1/2 mt-1.5 top-full
                   w-64 p-3 rounded-xl shadow-elev-3
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
