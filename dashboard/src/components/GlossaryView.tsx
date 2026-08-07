"use client";

import { useMemo, useState } from "react";
import { Card } from "./Cards";
import type { GlossaryEntry } from "@/lib/glossary-detail";
import type { Locale } from "@/lib/i18n";
import { t as translate } from "@/lib/i18n";

export function GlossaryView({
  entries,
  locale,
}: {
  entries: GlossaryEntry[];
  locale: Locale;
}) {
  const [query, setQuery] = useState("");
  const t = (k: string) => translate(locale, k);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter((e) => {
      const text = (
        locale === "he"
          ? `${e.term} ${e.term_he} ${e.explanation_he} ${e.formula} ${e.example_he}`
          : `${e.term} ${e.term_he} ${e.explanation_en} ${e.formula} ${e.example_en}`
      ).toLowerCase();
      return text.includes(q);
    });
  }, [query, entries, locale]);

  return (
    <>
      <Card className="mb-6">
        <input
          type="search"
          autoFocus
          placeholder={
            locale === "he"
              ? "חפש מונח (P/E, אלפא, NAV, ...)"
              : "Search a term (P/E, alpha, NAV, ...)"
          }
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full px-4 py-2.5 rounded-lg border border-council-200 dark:border-council-700 bg-white dark:bg-council-950 text-base"
        />
        {filtered.length === 0 && (
          <p className="text-sm text-muted mt-3 text-center">
            {locale === "he"
              ? "אין תוצאות התואמות את החיפוש."
              : "No results match your search."}
          </p>
        )}
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {filtered.map((e) => (
          <Card key={e.key} className="h-full">
            <div className="flex items-baseline justify-between gap-2 mb-2">
              <h3 className="text-lg font-semibold">
                {locale === "he" ? e.term_he : e.term}
              </h3>
              {locale === "he" && e.term !== e.term_he && (
                <span className="text-xs text-muted font-mono">
                  {e.term}
                </span>
              )}
            </div>
            <p className="text-sm text-council-700 dark:text-council-300 mb-4 leading-relaxed">
              {locale === "he" ? e.explanation_he : e.explanation_en}
            </p>
            <div className="rounded-md bg-council-50 dark:bg-council-800 px-3 py-2 mb-3 font-mono text-xs ltr">
              <span className="block text-[10px] uppercase tracking-wider text-muted mb-1">
                {locale === "he" ? "נוסחה" : "Formula"}
              </span>
              <span className="text-council-900 dark:text-council-100">
                {e.formula}
              </span>
            </div>
            <div className="text-sm">
              <span className="block text-[10px] uppercase tracking-wider text-muted mb-1">
                {locale === "he" ? "דוגמה" : "Example"}
              </span>
              <span className="text-council-700 dark:text-council-300">
                {locale === "he" ? e.example_he : e.example_en}
              </span>
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
