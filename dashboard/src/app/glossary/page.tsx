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

export const dynamic = "force-dynamic";

export default function GlossaryPage() {
  const { locale, t } = getServerI18n();
  return (
    <>
      <PageTitle title={t("glossary_title")} subtitle={t("glossary_subtitle")} />
      <GlossaryView entries={GLOSSARY_ENTRIES} locale={locale} />
    </>
  );
}
