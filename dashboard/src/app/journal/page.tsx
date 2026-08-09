// Decision journal, grouped by company.
//
// This was a flat feed of every decision row ever written — 1,240 of
// them for Benjamin Graham alone, in which ASGN appears 68 times, three
// times on one date. Read as transactions that is incomprehensible.
// They were never transactions: each row is the agent's verdict for
// that day, re-emitted on every run because the thesis does not change
// daily, and each carries shares=null and price=null.
//
// Grouped by ticker the same data reads as a short, honest story —
// "Graham flagged ASGN on 2026-05-06 and has reaffirmed it every
// trading day since" — and 1,240 rows become 41 cards. The raw feed is
// still one click away inside each card, where it belongs: as evidence,
// not as the front page.
//
// The rendering itself lives in ./JournalView, shared with the
// generated /journal/<agent>/<state> routes. This file is the landing
// view and picks the same default the query-string version did.

import { ANY, JournalView } from "./JournalView";

// Static in the two-language export, where "force-dynamic" is a hard
// error; dynamic under a live server (next dev). NEXT_PUBLIC_* is
// inlined at build time, so this folds to a constant.
export const dynamic = process.env.NEXT_PUBLIC_SITE_LOCALE
  ? "force-static"
  : "force-dynamic";

export default function JournalPage() {
  // Default to what is actually owned. Unfiltered this is 576 cards
  // across eleven agents, which trades one kind of overwhelm for
  // another; "held" is 210 and every one of them is a live commitment.
  return <JournalView agent={ANY} state="held" />;
}
