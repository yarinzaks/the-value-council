# The dashboard, in the cloud and private

The site used to exist only while a terminal window was open on one Mac.
This is how it became a thing you can open from a phone in another
country, that nobody else can open, that costs nothing, and that keeps
itself up to date without anyone touching it.

## What it is

Three separate machines, none of them yours:

1. **GitHub Actions** runs the agents four times a day and commits what
   they did back to this repository. It has done this since long before
   any of the below; the schedule is in
   `.github/workflows/daily-paper-trading.yml`.
2. **Cloudflare Pages** notices that commit, rebuilds the site from it,
   and publishes the result. This is the part that is new.
3. **Cloudflare Access** stands in front of the published site and asks
   for a one-time code sent to your email before letting anyone in.

The Mac is not involved in any of it.

## Why the site had to become static first

Cloudflare Pages serves files. It does not run a Node server, and the
dashboard was written as one: twelve pages declared `force-dynamic`,
read the language from a cookie on every request, and read their numbers
from `~/Library/Application Support/value-council` through Node's `fs`.
None of that exists on a build container.

Three things changed, and each is documented where it happens:

- **The data moved into the repository.** `data/backtest_results`,
  `data/prices`, `data/sectors.json` and `data/cache/company_names.json`
  are committed now, and the daily workflow keeps the last two fresh.
  `resolveDataRoot()` in `dashboard/src/lib/data.ts` checks that the Mac
  path exists before preferring it, which it does not on Linux.
- **The language stopped being a cookie.** `NEXT_PUBLIC_SITE_LOCALE`
  fixes it at build time, so the site is built twice and published as
  `/he` and `/en` side by side. Server-rendered Hebrew survives, with no
  flash of English, because each page really is Hebrew on disk. The
  toggle navigates between the two (`swapLocalePath` in
  `components/Providers.tsx`).
- **The journal's filters became routes.** A static export cannot read
  `?agent=…&state=…`, and the obvious fix — ship every card, hide the
  rest in the browser — does not survive the numbers: unfiltered, that
  page is 5.5 MB of HTML, because every card carries its full decision
  timeline. All forty-eight combinations are generated instead, so each
  page stays the size it was.

The build is `dashboard/scripts/build-static-site.sh`. It refuses to run
if the data tree is missing, rather than publishing a complete site with
no numbers in it — every loader in `data.ts` returns `[]` on a read
error by design, so a blank build would otherwise look like a success.

## Setting it up on Cloudflare

This part needs your account, so it is yours to do. It is done once.

### 1. Create the Pages project

In the Cloudflare dashboard: **Workers & Pages → Create → Pages →
Connect to Git**, and pick this repository.

Build settings:

| Field | Value |
| --- | --- |
| Framework preset | None |
| Build command | `cd dashboard && npm ci && ./scripts/build-static-site.sh` |
| Build output directory | `dashboard/site` |
| Root directory | *(leave empty — the repository root)* |

Add one environment variable, or the build will use whatever Node
Cloudflare defaults to:

| Variable | Value |
| --- | --- |
| `NODE_VERSION` | `20` |

Deploy. The first build takes a few minutes, most of it spent
prerendering the journal.

### 2. Put the door in front of it

Still in Cloudflare: **Zero Trust → Access → Applications → Add an
application → Self-hosted**.

- Application domain: the `*.pages.dev` hostname the project was given.
- Add a policy: action **Allow**, rule **Emails**, value *your email
  address*.
- Identity provider: **One-time PIN**. This needs no Google or GitHub
  login — Cloudflare emails a code that expires in ten minutes.

Free for up to 50 users. You are one.

### 3. Check it

Open the site in a private window. You should be asked for your email,
then for the code. Anyone without an address on that policy gets a
refusal rather than the dashboard.

## What happens from then on

The agents trade, the workflow commits, Cloudflare rebuilds, and the
site is current — four times a day, whether or not the Mac is switched
on. Nothing needs to be run by hand.

## What this deliberately does not do

- **The repository stays public.** Public repositories get unlimited
  free Actions minutes; private ones get 2,000 a month against measured
  usage of ~940 in a normal week and ~1,790 in a heavy one. Making it
  private would risk the agents stopping mid-month, and the trade is not
  worth it for a paper-trading project with no secrets in it. The site
  is private either way — that was the actual requirement.
- **Position pages exist for open positions only.** They are generated
  from the current portfolios, which is also the only place anything
  links to them.
- **`data/sectors.json` is `{"HOLD": "unknown"}`** and has been for some
  time, so the sector donut has nothing to draw. That is a pre-existing
  bug in the export, not something this change introduced, and it will
  ship as-is until it is fixed.
