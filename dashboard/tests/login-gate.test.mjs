// Tests for the login in front of the published dashboard.
//
// The gate this replaced had none, which is how it shipped with a
// property nobody had written down: Basic auth cannot be logged out of.
// Its check was sound and its design was not, and no test said so
// because no test existed.
//
// Run with `npm test` from dashboard/ — `node --test`, the runner built
// into Node, so this suite adds no dependency.
//
// The file under test lives outside dashboard/ because Cloudflare Pages
// deploys it from the repository root, and every module under functions/
// becomes a route. `_middleware.js` is the one filename Cloudflare
// documents as not being one, which is why the session helpers live in
// it rather than in a functions/_lib/ whose routing is not documented
// anywhere.

import test from "node:test";
import assert from "node:assert/strict";

import {
  COOKIE_NAME,
  SESSION_TTL_SECONDS,
  clearCookie,
  issueSession,
  loginPage,
  onRequest,
  readCookie,
  safeEqual,
  safeNext,
  setCookie,
  verifySession,
} from "../../functions/_middleware.js";

const ENV = { SITE_USER: "yarin", SITE_PASSWORD: "a-long-random-password" };

/** A `next()` that records whether the gate ever let a request past. */
function spy(body = "SECRET PAGE") {
  const calls = { n: 0 };
  const next = async () => {
    calls.n++;
    return new Response(body, { status: 200 });
  };
  return { next, calls };
}

function get(path, headers = {}) {
  return new Request(`https://council.pages.dev${path}`, { headers });
}

function post(path, fields, headers = {}) {
  const body = new URLSearchParams(fields);
  return new Request(`https://council.pages.dev${path}`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded", ...headers },
  });
}

async function session(env = ENV, now = Date.now()) {
  return `${COOKIE_NAME}=${await issueSession(env, env.SITE_USER, now)}`;
}

// ------------------------------------------------- nothing gets past

test("an unauthenticated request never reaches the site", async () => {
  // The paths that matter: a page, a build asset, the assistant's
  // endpoint, and the JSON that endpoint reads. One of these being
  // public would make the others pointless.
  for (const path of [
    "/he/",
    "/en/agents/",
    "/he/_next/static/chunks/main.js",
    "/api/chat",
    "/chat-context.json",
    "/_redirects",
    "/index.html",
  ]) {
    const { next, calls } = spy();
    const res = await onRequest({ request: get(path), env: ENV, next });
    assert.equal(calls.n, 0, `${path} reached the site`);
    assert.ok(res.status === 303 || res.status === 401, `${path} -> ${res.status}`);
  }
});

test("cancelling the login does not let anyone browse", async () => {
  // The founder's report, as a test. Basic auth sent no Authorization
  // header on Cancel and the gate refused — correctly — but the browser
  // had already cached the credentials from an earlier sign-in, so
  // nothing visible changed. A session cookie has no such shadow: no
  // cookie is no access, every time.
  const { next, calls } = spy();
  const res = await onRequest({ request: get("/he/"), env: ENV, next });
  assert.equal(calls.n, 0);
  assert.equal(res.status, 303);
  assert.match(res.headers.get("Location"), /^\/login\?next=/);
});

test("a cookie the browser invented is refused", async () => {
  const forged = [
    "council_session=x",
    "council_session=.",
    "council_session=eyJ1IjoieWFyaW4ifQ.", // payload, empty signature
    "council_session=eyJ1IjoieWFyaW4ifQ.AAAA", // payload, wrong signature
    `council_session=${await issueSession({ SITE_PASSWORD: "different" }, "yarin")}`,
  ];
  for (const cookie of forged) {
    const { next, calls } = spy();
    const res = await onRequest({
      request: get("/he/", { Cookie: cookie }),
      env: ENV,
      next,
    });
    assert.equal(calls.n, 0, cookie);
    assert.equal(res.status, 303, cookie);
  }
});

test("a tampered payload invalidates the signature", async () => {
  const token = await issueSession(ENV, "yarin");
  const [payload, signature] = token.split(".");
  // Re-sign nothing: swap the claims and keep the old signature.
  const other = Buffer.from(JSON.stringify({ u: "root", exp: 9e9 }))
    .toString("base64url");
  assert.equal(await verifySession(ENV, `${other}.${signature}`), null);
  assert.ok(await verifySession(ENV, `${payload}.${signature}`));
});

test("the site stays shut when it is not configured", async () => {
  for (const env of [{}, { SITE_USER: "yarin" }, { SITE_PASSWORD: "x" }]) {
    const { next, calls } = spy();
    const res = await onRequest({ request: get("/he/"), env, next });
    assert.equal(calls.n, 0);
    assert.equal(res.status, 503);
  }
});

// ------------------------------------------------------ signing in

test("the right credentials set a session and send the reader on", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: post("/login", {
      user: "yarin",
      password: "a-long-random-password",
      next: "/he/agents/",
    }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 0, "the login page is served by the gate, not the site");
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("Location"), "/he/agents/");
  const cookie = res.headers.get("Set-Cookie");
  assert.match(cookie, /^council_session=[\w.-]+;/);
  assert.match(cookie, /HttpOnly/, "a script must not be able to read it");
  assert.match(cookie, /Secure/, "it must not cross plain HTTP");
  assert.match(cookie, /SameSite=Lax/, "another site must not ride it");
  assert.match(cookie, /Path=\//);
});

test("that session then opens the site", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: get("/he/", { Cookie: await session() }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 1);
  assert.equal(res.status, 200);
  assert.equal(await res.text(), "SECRET PAGE");
  assert.equal(res.headers.get("Cache-Control"), "private, no-store");
});

test("wrong credentials say which neither half was", async () => {
  for (const fields of [
    { user: "yarin", password: "wrong" },
    { user: "nobody", password: "a-long-random-password" },
    { user: "", password: "" },
    { user: "yarin" },
  ]) {
    const { next, calls } = spy();
    const res = await onRequest({ request: post("/login", fields), env: ENV, next });
    assert.equal(calls.n, 0);
    assert.equal(res.status, 401);
    const body = await res.text();
    assert.match(body, /שם משתמש או סיסמה שגויים/);
    assert.ok(!res.headers.get("Set-Cookie"), "no cookie on a failed login");
    // The one message covers both, so the form is not a username oracle.
    assert.ok(!/משתמש לא נמצא|סיסמה שגויה בלבד/.test(body));
  }
});

test("the login page is reachable without a session", async () => {
  const { next, calls } = spy();
  const res = await onRequest({ request: get("/login"), env: ENV, next });
  assert.equal(calls.n, 0);
  assert.equal(res.status, 200);
  assert.match(res.headers.get("Content-Type"), /text\/html/);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
});

// -------------------------------------------------------- logging out

test("logging out erases the cookie", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: get("/logout", { Cookie: await session() }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 0);
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("Location"), "/login");
  assert.match(res.headers.get("Set-Cookie"), /council_session=;/);
  assert.match(res.headers.get("Set-Cookie"), /Max-Age=0/);
});

test("the erased cookie no longer opens anything", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: get("/he/", { Cookie: `${COOKIE_NAME}=` }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 0);
  assert.equal(res.status, 303);
});

// ----------------------------------------------------------- expiry

test("a session expires", async () => {
  const issued = Date.now();
  const token = await issueSession(ENV, "yarin", issued);
  assert.ok(await verifySession(ENV, token, issued + 1000));
  assert.ok(await verifySession(ENV, token, issued + (SESSION_TTL_SECONDS - 60) * 1000));
  assert.equal(
    await verifySession(ENV, token, issued + (SESSION_TTL_SECONDS + 60) * 1000),
    null,
    "a twelve-hour session must not outlive twelve hours",
  );
});

test("changing the password revokes every session", async () => {
  // The revocation Basic auth could not offer, and the button to press
  // if a laptop goes missing.
  const token = await issueSession(ENV, "yarin");
  assert.ok(await verifySession(ENV, token));
  assert.equal(
    await verifySession({ ...ENV, SITE_PASSWORD: "rotated" }, token),
    null,
  );
});

test("SESSION_SECRET decouples sessions from the password", async () => {
  const env = { ...ENV, SESSION_SECRET: "an-independent-value" };
  const token = await issueSession(env, "yarin");
  // The password may now be changed without signing the reader out.
  assert.ok(await verifySession({ ...env, SITE_PASSWORD: "rotated" }, token));
  // And a token signed with the password alone is no longer accepted.
  assert.equal(await verifySession(env, await issueSession(ENV, "yarin")), null);
});

// ------------------------------------------------- the redirect target

test("the login form cannot be turned into an open redirector", async () => {
  for (const hostile of [
    "//evil.example/",
    "//evil.example",
    "https://evil.example/",
    "\\\\evil.example",
    "/\\evil.example",
    "evil.example",
    "",
    null,
    undefined,
    42,
  ]) {
    assert.equal(safeNext(hostile), "/he/", JSON.stringify(hostile));
  }
});

test("a character the URL parser deletes cannot smuggle in an origin", async () => {
  // The hole the first version left. WHATWG strips tab and newline from
  // a URL *before* parsing it, so "/\t/evil.example" is parsed as
  // "//evil.example" — protocol-relative, and a browser resolves that
  // against the scheme and nothing else. The guard tested for two
  // literal slashes and never considered the character between them
  // vanishing.
  //
  // Reproduced: tab, LF and CR all reached https://evil.example.
  const hostile = [
    "/\t/evil.example",
    "/\n/evil.example",
    "/\r/evil.example",
    "/\u0000/evil.example",
    "/ /evil.example",
    "/\t\t//evil.example",
    "/\u000b/evil.example",
    "/\u000c/evil.example",
    "/\u007f/evil.example",
  ];
  for (const raw of hostile) {
    assert.equal(safeNext(raw), "/he/", JSON.stringify(raw));
  }
});

test("whatever safeNext returns stays on this origin", async () => {
  // The property, asserted directly rather than by enumerating the
  // characters that break it — which is the enumeration that went
  // wrong. Anything the function lets through must resolve to this
  // site when a browser resolves it.
  const SITE = "https://council.pages.dev";
  const inputs = [
    "/he/", "/en/agents/", "/he/agents/john_neff/?tab=trades",
    "/he/x#frag", "/a/../b", "/a/./b", "//evil.example", "///evil.example",
    "/\t/evil.example", "/\n//evil.example", "/\\evil.example",
    "https://evil.example", "http://evil.example", "javascript:alert(1)",
    "/%2f%2fevil.example", "/he/\u2028/evil.example", "/?next=//evil.example",
    "/#//evil.example", "/\u0085/evil.example", "/\ufeff/evil.example",
  ];
  for (const raw of inputs) {
    const allowed = safeNext(raw);
    assert.ok(allowed.startsWith("/"), `${raw} -> ${allowed}`);
    const resolved = new URL(allowed, SITE);
    assert.equal(resolved.origin, SITE, `${JSON.stringify(raw)} -> ${allowed}`);
  }
});

test("normalisation cannot produce an origin the input did not have", async () => {
  // The second hole, and the same mistake one level in. The origin
  // check looks at the *input*; collapsing `.` and `..` segments can
  // *produce* a leading `//` in the output. `/a/..//evil.example` parses
  // with an origin of SELF — the base supplied it — and a pathname of
  // `//evil.example`, which a browser then resolves off-site. Six
  // inputs reached https://evil.example through a guard built to stop
  // exactly that.
  for (const raw of [
    "/.//evil.example",
    "/a/..//evil.example",
    "/%2e//evil.example",
    "/a/b/../..//evil.example",
    "/./././//evil.example",
    "/a/..///evil.example",
  ]) {
    assert.equal(safeNext(raw), "/he/", raw);
  }
});

test("no combination of path syntax escapes this origin", async () => {
  // Generated, not listed. Twice the guard was wrong and twice the
  // test that should have caught it was the right shape with an input
  // set that came out of my head — `/a/../b` was in it, and
  // `/a/..//evil.example` was not.
  //
  // So the inputs are built from the fragments that matter, in every
  // order, and the property is asserted over all of them. The next
  // variant of this fails here rather than in a Location header.
  const SITE = "https://council.pages.dev";
  const FRAGMENTS = [
    "/", "//", "///", ".", "..", "%2e", "%2e%2e", "%2f", "\\",
    "\t", "\n", "\r", " ", "a", "evil.example", "@evil.example", ":",
  ];

  let checked = 0;
  const build = (parts) => parts.join("");
  for (const a of FRAGMENTS) {
    for (const b of FRAGMENTS) {
      for (const c of FRAGMENTS) {
        for (const candidate of [
          build(["/", a, b, c]),
          build(["/", a, "/", b, c]),
          build(["/he/", a, b, "/", c]),
        ]) {
          const out = safeNext(candidate);
          checked++;
          assert.ok(
            typeof out === "string" && out.startsWith("/"),
            `${JSON.stringify(candidate)} -> ${JSON.stringify(out)}`,
          );
          let origin;
          try {
            origin = new URL(out, SITE).origin;
          } catch {
            origin = "(unparseable)";
          }
          assert.equal(
            origin,
            SITE,
            `${JSON.stringify(candidate)} -> ${JSON.stringify(out)} -> ${origin}`,
          );
        }
      }
    }
  }
  assert.ok(checked > 10_000, `only ${checked} combinations checked`);
});

test("a legitimate path survives intact", async () => {
  assert.equal(safeNext("/he/agents/john_neff/"), "/he/agents/john_neff/");
  assert.equal(
    safeNext("/en/journal/?state=open"),
    "/en/journal/?state=open",
  );
  assert.equal(safeNext("/he/x#section"), "/he/x#section");
});

test("a path on this site is carried through", async () => {
  assert.equal(safeNext("/he/agents/benjamin_graham/"), "/he/agents/benjamin_graham/");
  assert.equal(safeNext("/en/journal/?state=open"), "/en/journal/?state=open");
});

test("signing in cannot land the reader back on the login page", async () => {
  for (const loop of ["/login", "/login/", "/logout", "/login?next=/login"]) {
    assert.equal(safeNext(loop), "/he/", loop);
  }
});

test("where the reader was going is carried into the form", async () => {
  const { next } = spy();
  const res = await onRequest({
    request: get("/he/agents/john_neff/?tab=trades"),
    env: ENV,
    next,
  });
  assert.equal(res.status, 303);
  assert.equal(
    res.headers.get("Location"),
    `/login?next=${encodeURIComponent("/he/agents/john_neff/?tab=trades")}`,
  );
});

// ------------------------------------------------------- the endpoint

test("an API call gets a status rather than a redirect", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: new Request("https://council.pages.dev/api/chat", { method: "POST" }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 0, "the assistant must not run for a stranger");
  assert.equal(res.status, 401);
  assert.match(res.headers.get("Content-Type"), /application\/json/);
});

test("a signed-in API call is let through", async () => {
  const { next, calls } = spy();
  const res = await onRequest({
    request: new Request("https://council.pages.dev/api/chat", {
      method: "POST",
      headers: { Cookie: await session() },
    }),
    env: ENV,
    next,
  });
  assert.equal(calls.n, 1);
});

// --------------------------------------------------------- the pieces

test("the cookie reader takes the named one out of a crowd", async () => {
  assert.equal(readCookie("a=1; council_session=TOKEN; b=2"), "TOKEN");
  assert.equal(readCookie("council_session=TOKEN"), "TOKEN");
  assert.equal(readCookie("  council_session = TOKEN  "), "TOKEN");
  assert.equal(readCookie("other=TOKEN"), null);
  assert.equal(readCookie(""), null);
  assert.equal(readCookie(null), null);
  // A cookie whose name merely contains the real one must not match.
  assert.equal(readCookie("not_council_session=TOKEN"), null);
});

test("the comparison is length-safe and value-correct", async () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "abcd"), false);
  assert.equal(safeEqual("", ""), true);
  // Non-ASCII, which an earlier gate got wrong.
  assert.equal(safeEqual("סיסמה", "סיסמה"), true);
  assert.equal(safeEqual("סיסמה", "סיסמא"), false);
});

test("a password outside ASCII works end to end", async () => {
  const env = { SITE_USER: "ירין", SITE_PASSWORD: "סיסמה-ארוכה-מאוד" };
  const res = await onRequest({
    request: post("/login", { user: "ירין", password: "סיסמה-ארוכה-מאוד" }),
    env,
    next: spy().next,
  });
  assert.equal(res.status, 303);
  assert.match(res.headers.get("Set-Cookie"), /^council_session=[\w.-]+;/);
});

test("the form escapes what it echoes back", async () => {
  // `next` is reflected into a hidden input. Unescaped, a crafted link
  // would put markup on the one page served without a session.
  const page = loginPage({ next: '/he/"><script>alert(1)</script>' });
  assert.ok(!page.includes("<script>alert(1)</script>"));
  assert.ok(page.includes("&quot;&gt;&lt;script&gt;"));
});

test("the login page forbids scripts outright", async () => {
  const res = await onRequest({ request: get("/login"), env: ENV, next: spy().next });
  const csp = res.headers.get("Content-Security-Policy");
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /form-action 'self'/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.equal(res.headers.get("X-Frame-Options"), "DENY");
});

test("the cookie helpers say what they mean", async () => {
  assert.match(setCookie("T"), /^council_session=T; Path=\/; Max-Age=43200;/);
  assert.match(clearCookie(), /Max-Age=0/);
  assert.equal(SESSION_TTL_SECONDS, 43200);
});
