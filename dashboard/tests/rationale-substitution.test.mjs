// A translated rationale must keep every number the English one carried.
//
// The closed-position rationale lost both of its prices on every page
// that showed one. The Hebrew read "כניסה $1, יציאה $2" — not the
// entry and exit, but the literal characters of the backreferences.
//
// The cause is one character. In a `String.replace` replacement, `$$`
// emits a literal dollar sign, so `$$1` is "$" followed by "1" and not
// "$" followed by capture group 1. Writing a price needs `$$$1`: the
// `$$` for the sign, then `$1` for the group. The percentage beside it
// was written `$3`, needed no dollar sign, and substituted correctly —
// which is why the bug looked like it only ate the money.
//
// Two tests, because one of them alone would not have caught it. The
// behavioural one proves the rationale a reader actually sees is whole;
// the source scan fails on any rule that acquires the same defect
// later, including rules whose output is currently discarded for other
// reasons and so cannot be caught by rendering.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { translateRationale } from "../src/lib/translate-dynamic.ts";

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
  join(HERE, "../src/lib/translate-dynamic.ts"),
  "utf8",
);

test("a closed-position rationale keeps its entry and exit price", () => {
  const english =
    "Closed live position: entry $101.53, exit $102.03 (+0.49%)";
  const hebrew = translateRationale(english, "he");

  assert.notEqual(hebrew, english, "the rule did not fire at all");
  assert.ok(
    hebrew.includes("101.53"),
    `entry price lost: ${JSON.stringify(hebrew)}`,
  );
  assert.ok(
    hebrew.includes("102.03"),
    `exit price lost: ${JSON.stringify(hebrew)}`,
  );
  assert.ok(
    hebrew.includes("0.49"),
    `percentage lost: ${JSON.stringify(hebrew)}`,
  );
});

test("a net-net rationale keeps its NCAV per share", () => {
  const english =
    "P/NCAV 0.55 (≤ ⅔), NCAV/share $12.40. Classic Graham Net-Net.";
  const hebrew = translateRationale(english, "he");

  assert.ok(
    hebrew.includes("12.40"),
    `NCAV per share lost: ${JSON.stringify(hebrew)}`,
  );
});

test("no replacement writes a literal backreference where it means a price", () => {
  // `$$1` emits the text "$1"; a price needs `$$$1`. Match a `$$`
  // followed by a digit that is not itself preceded by a third `$`.
  // Only the replacement strings themselves — a comment is free to
  // spell the broken form out, and this test's own file does.
  const offenders = [];
  SOURCE.split("\n").forEach((line, i) => {
    const replacement = line.match(/^\s*he:\s*"(.*)",?\s*$/);
    if (!replacement) return;
    for (const m of replacement[1].matchAll(/(?<!\$)\$\$(\d)/g)) {
      offenders.push(`${i + 1}: ${line.trim()}  (writes the text "$${m[1]}")`);
    }
  });

  assert.deepEqual(
    offenders,
    [],
    `a replacement emits a literal backreference instead of a value:\n${offenders.join("\n")}`,
  );
});
