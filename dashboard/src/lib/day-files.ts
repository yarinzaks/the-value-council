// Which files in a per-day directory to actually read.
//
// Every store under data/ is one JSON file per agent per day, and the
// loaders used to take the directory listing at face value: filter on
// ".json", sort, read all of them. That is correct until the directory
// holds two files for one day.
//
// It does. macOS file sync resolves a write collision by keeping both
// sides and renaming one of them "<name> 2.json"; twenty-three such
// files sit under data/decisions and data/trades, all stemmed
// 2026-10-30, each byte-identical to the original beside it. Reading
// both counted that session twice — 404 decision rows and 34 trades —
// which doubled a day's exit count and printed every ticker twice in
// the agent's recent-decisions list.
//
// .gitignore has a block of "* [0-9].<ext>" rules for exactly these,
// so git never showed them; readdir does. The rules hid the symptom
// from one reader and left it for another, which is why the filter
// belongs here rather than in a cleanup someone has to remember.

/** A sync-collision copy: "<day> 2.json" beside "<day>.json". */
const COLLISION_COPY = / (\d+)$/;

/**
 * The files to read from a per-day directory: one per day, sorted.
 *
 * A day with both an original and collision copies yields the
 * original. A day with only copies yields the lowest-numbered one —
 * dropping the day entirely would lose real rows, which is the worse
 * of the two failures.
 */
export function canonicalDayFiles(files: readonly string[]): string[] {
  const best = new Map<string, { file: string; rank: number }>();

  for (const file of files) {
    if (!file.endsWith(".json")) continue;
    const stem = file.slice(0, -".json".length);
    const copy = COLLISION_COPY.exec(stem);
    // rank 0 is the original; a copy ranks by its own number, so the
    // lowest survives when the original is gone.
    const day = copy ? stem.slice(0, -copy[0].length) : stem;
    const rank = copy ? Number(copy[1]) : 0;

    const held = best.get(day);
    if (!held || rank < held.rank) best.set(day, { file, rank });
  }

  return [...best.values()].map((v) => v.file).sort();
}
