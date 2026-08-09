// /glossary route — searchable financial-term reference.
//
// Server-rendered shell, client-side filter. Each term card shows:
//   - Locale-aware term name
//   - Two-line plain-language explanation
//   - Universal formula
//   - Worked example

import { Card, PageTitle } from "@/components/Cards";
import { GlossaryView } from "@/components/GlossaryView";
import { GLOSSARY_ENTRIES } from "@/lib/glossary-detail";
import { getServerI18n } from "@/lib/locale-server";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

export default function GlossaryPage() {
  const { locale, t } = getServerI18n();
  return (
    <>
      <PageTitle title={t("glossary_title")} subtitle={t("glossary_subtitle")} />
      <GlossaryView entries={GLOSSARY_ENTRIES} locale={locale} />
    </>
  );
}
