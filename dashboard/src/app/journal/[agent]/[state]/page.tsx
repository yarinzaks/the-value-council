// One journal view per filter combination.
//
// The filters used to be a query string, which a static export cannot
// read. Every combination the chips can reach is generated instead —
// twelve agent values by four lifecycle values — so the same clicks
// lead to the same content with the same URLs being shareable.

import { JournalView, filterCombinations } from "../../JournalView";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

export function generateStaticParams(): Array<{ agent: string; state: string }> {
  return filterCombinations();
}

export default function FilteredJournalPage({
  params,
}: {
  params: { agent: string; state: string };
}) {
  // JournalView treats an unrecognised value on either axis as "do not
  // filter", so a hand-typed URL degrades to a wider view rather than
  // an error.
  return <JournalView agent={params.agent} state={params.state} />;
}
