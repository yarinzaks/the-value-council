// The login in front of the published dashboard.
//
// What was here before, and why it was replaced
// ---------------------------------------------
//
// HTTP Basic authentication. Its logic was sound — driven with forty
// probe cases it never once called `next()` without the right
// credentials, and it failed closed when unconfigured. The problem was
// not the check. It was that Basic auth has no session:
//
//   * There is no log out. None. The browser holds the credentials for
//     the rest of its lifetime and attaches them to every request to
//     this origin by itself, so pressing Cancel on a prompt that
//     appears later clears nothing.
//   * There is no expiry. A laptop left open stays signed in.
//   * There is no revocation except changing the password, which signs
//     the one legitimate reader out as well.
//
// The founder's report was "I press Cancel and can still browse, as if
// the login is not really worth anything." The gate was doing what it
// was built to do; what it was built to do was not enough.
//
// What this is
// ------------
//
// A login form, and a cookie carrying the user and an expiry, signed
// with HMAC-SHA256 so a browser cannot mint one. Verification is a
// signature check and a clock check and nothing else — no storage,
// which is what keeps it inside a Pages Function with no database and
// no subscription.
//
// Cloudflare Access is still the better tool, and still wants a payment
// method on file even on its free tier. The brief was "private, and it
// must never cost me anything".
//
// Setting it up
// -------------
//
// In the Cloudflare dashboard: the Pages project -> Settings ->
// Variables and Secrets -> add these for Production and Preview:
//
//     SITE_USER        whatever you like, e.g. yarin
//     SITE_PASSWORD    a long random one; choose "Secret" so it is encrypted
//     SESSION_SECRET   optional; a second long random value
//
// They live there and only there. None appears in this repository, in
// the build, or in any log — the repository is public, so a password
// committed here would be a password published.
//
// Without SESSION_SECRET the signing key is derived from SITE_PASSWORD,
// which has a useful consequence: changing the password invalidates
// every session that exists. That is the revocation Basic auth could
// not offer, and it is the button to press if a laptop goes missing.

/** Twelve hours — a trading day, and short enough that a browser left
 *  open overnight asks again in the morning. */
export const SESSION_TTL_SECONDS = 12 * 60 * 60;

export const COOKIE_NAME = "council_session";

/** Paths the gate serves itself, and must therefore not demand a
 *  session for. Exact matches only: a prefix test would have made
 *  `/login-bypass/anything` public. */
const PUBLIC_PATHS = new Set(["/login", "/login/", "/logout", "/logout/"]);

const encoder = new TextEncoder();

// ---------------------------------------------------------------- bytes

/** base64url: a cookie value may not carry `+`, `/` or `=`. */
function toBase64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function fromBase64Url(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
}

/**
 * Compare two byte arrays without leaking their common prefix through
 * timing. `===` returns as soon as it finds a difference, which over
 * enough requests reveals a secret one byte at a time.
 */
export function safeEqualBytes(left, right) {
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left[i] ^ right[i];
  return diff === 0;
}

/** Constant-time comparison of two strings, as UTF-8 bytes. */
export function safeEqual(a, b) {
  return safeEqualBytes(encoder.encode(a), encoder.encode(b));
}

// -------------------------------------------------------------- session

async function signingKey(env) {
  const material = env.SESSION_SECRET || env.SITE_PASSWORD;
  if (!material) return null;
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(`council-session-v1:${material}`),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

/**
 * A cookie value for ``user``, valid for :data:`SESSION_TTL_SECONDS`.
 *
 * ``now`` is injected so a test can assert expiry without waiting
 * twelve hours for it.
 */
export async function issueSession(env, user, now = Date.now()) {
  const key = await signingKey(env);
  if (!key) return null;
  const payload = toBase64Url(
    encoder.encode(
      JSON.stringify({ u: user, exp: Math.floor(now / 1000) + SESSION_TTL_SECONDS }),
    ),
  );
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(payload)),
  );
  return `${payload}.${toBase64Url(signature)}`;
}

/**
 * The claims a cookie carries, or ``null`` when it carries none.
 *
 * Null for every way a token can be wrong — absent, malformed, forged,
 * expired — and deliberately without saying which. A caller that told
 * them apart would be an oracle; this one just shows the form again.
 */
export async function verifySession(env, value, now = Date.now()) {
  if (!value || typeof value !== "string") return null;
  const dot = value.indexOf(".");
  if (dot <= 0 || dot === value.length - 1) return null;
  const payload = value.slice(0, dot);

  const key = await signingKey(env);
  if (!key) return null;

  let provided;
  try {
    provided = fromBase64Url(value.slice(dot + 1));
  } catch {
    return null;
  }
  const expected = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(payload)),
  );
  if (!safeEqualBytes(provided, expected)) return null;

  let claims;
  try {
    claims = JSON.parse(new TextDecoder().decode(fromBase64Url(payload)));
  } catch {
    return null;
  }
  if (typeof claims?.exp !== "number" || claims.exp * 1000 <= now) return null;
  if (typeof claims?.u !== "string" || !claims.u) return null;
  return claims;
}

export function setCookie(value, { ttl = SESSION_TTL_SECONDS } = {}) {
  return `${COOKIE_NAME}=${value}; Path=/; Max-Age=${ttl}; HttpOnly; Secure; SameSite=Lax`;
}

/** Max-Age=0 is what a browser erases on. */
export function clearCookie() {
  return `${COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`;
}

/** One named cookie out of a Cookie header, or null. */
export function readCookie(header, name = COOKIE_NAME) {
  if (!header) return null;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim();
  }
  return null;
}

// ----------------------------------------------------------- the pages

/** A sentinel to resolve a candidate path against.
 *
 *  `.invalid` is reserved by RFC 2606 and can never be a real host, so
 *  a candidate that resolves to some *other* origin is one that
 *  carried an origin of its own.
 */
const SELF = "https://self.invalid";

/** Where to send the reader after signing in.
 *
 *  Only a path on this site. Anything else is an open redirector: a
 *  crafted `/login?next=…` link, a victim who signs in, and the 303
 *  lands them on the attacker's page — having just arrived from a
 *  domain they trust and typed a password into it.
 *
 *  Checked by resolving the candidate and comparing origins, not by
 *  listing the characters that break it. The first version did list
 *  them — it refused a leading `//` and any backslash — and it was
 *  wrong, because the URL spec *deletes* tab, LF and CR from the input
 *  before parsing. `/⇥/evil.example` is parsed as `//evil.example`,
 *  which is protocol-relative, and a browser resolved it to
 *  https://evil.example. Tab, LF and CR all got through, confirmed by
 *  running them.
 *
 *  Resolving instead of matching is the structural answer: whatever
 *  the parser does to the input, the origin it lands on is the thing
 *  that matters, and that is the thing compared. Control characters
 *  and whitespace are refused outright as well — not because the
 *  origin check needs the help, but because a path that contains one
 *  is not a path this site ever produced, and a value that reaches a
 *  `Location` header should not carry a byte that could split it.
 */
export function safeNext(raw) {
  if (typeof raw !== "string" || !raw.startsWith("/")) return "/he/";
  // C0 controls, space, DEL, and the backslash some parsers fold to `/`.
  if (/[\u0000-\u0020\u007f\\]/.test(raw)) return "/he/";
  let resolved;
  try {
    resolved = new URL(raw, SELF);
  } catch {
    return "/he/";
  }
  if (resolved.origin !== SELF) return "/he/";
  if (PUBLIC_PATHS.has(resolved.pathname)) return "/he/";
  // Reassembled from the parsed parts, so what is returned is what a
  // browser will have resolved — `/a/../b` normalised, and nothing
  // ambiguous left in it.
  return `${resolved.pathname}${resolved.search}${resolved.hash}`;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** The form. Hebrew and right-to-left, like everything behind it. */
export function loginPage({ next = "/he/", error = "" } = {}) {
  return `<!doctype html>
<html lang="he" dir="rtl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>מועצת הערך — התחברות</title>
<style>
  :root { color-scheme: light dark; --bg:#f8fafc; --fg:#0f172a; --card:#fff;
          --line:#e2e8f0; --muted:#64748b; --accent:#1e293b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0b1120; --fg:#e2e8f0; --card:#131c31; --line:#1f2a44;
            --muted:#94a3b8; --accent:#e2e8f0; }
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100dvh; display:grid; place-items:center;
         background:var(--bg); color:var(--fg); padding:16px;
         font:16px/1.5 ui-sans-serif, system-ui, "Segoe UI", Arial, sans-serif; }
  form { width:100%; max-width:360px; background:var(--card);
         border:1px solid var(--line); border-radius:14px; padding:28px 24px; }
  h1 { margin:0 0 4px; font-size:19px; }
  p.sub { margin:0 0 22px; color:var(--muted); font-size:13px; }
  label { display:block; font-size:13px; margin:14px 0 6px; color:var(--muted); }
  input { width:100%; padding:11px 12px; font-size:16px; border-radius:9px;
          border:1px solid var(--line); background:var(--bg); color:var(--fg); }
  button { width:100%; margin-top:22px; padding:12px; font-size:15px;
           font-weight:600; border:0; border-radius:9px; cursor:pointer;
           background:var(--accent); color:var(--bg); }
  .err { margin:16px 0 0; padding:10px 12px; border-radius:9px; font-size:13px;
         background:#fee2e2; color:#991b1b; }
  @media (prefers-color-scheme: dark) { .err { background:#45161b; color:#fca5a5; } }
</style>
<form method="POST" action="/login">
  <h1>מועצת הערך</h1>
  <p class="sub">הדשבורד פרטי. נדרשת התחברות.</p>
  <input type="hidden" name="next" value="${escapeHtml(next)}">
  <label for="u">שם משתמש</label>
  <input id="u" name="user" autocomplete="username" autocapitalize="off"
         autocorrect="off" spellcheck="false" required autofocus>
  <label for="p">סיסמה</label>
  <input id="p" name="password" type="password"
         autocomplete="current-password" required>
  <button type="submit">התחברות</button>
  ${error ? `<p class="err">${escapeHtml(error)}</p>` : ""}
</form>
`;
}

const HTML_HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": "no-store",
  // The login page needs no scripts and loads nothing from anywhere
  // else, so it says so: an injected <script> cannot run even if one
  // ever reached the markup.
  "Content-Security-Policy":
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; " +
    "base-uri 'none'; frame-ancestors 'none'",
  "Referrer-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

function html(body, status = 200, extra = {}) {
  return new Response(body, { status, headers: { ...HTML_HEADERS, ...extra } });
}

function unconfigured() {
  return html(
    "<!doctype html><meta charset=utf-8><p>This site is not configured yet. " +
      "Set SITE_USER and SITE_PASSWORD in the Pages project settings.",
    503,
  );
}

// ------------------------------------------------------------ the gate

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const path = url.pathname;

  // Fail closed. A fresh deploy, a typo in a variable name, a preview
  // branch nobody configured — the site stays shut rather than quietly
  // serving to everyone. There is no state in which it is open by
  // accident.
  if (!env.SITE_USER || !env.SITE_PASSWORD) return unconfigured();

  // ---- log out ----------------------------------------------------
  if (path === "/logout" || path === "/logout/") {
    return new Response(null, {
      status: 303,
      headers: {
        Location: "/login",
        "Set-Cookie": clearCookie(),
        "Cache-Control": "no-store",
      },
    });
  }

  // ---- log in -----------------------------------------------------
  if (path === "/login" || path === "/login/") {
    if (request.method === "GET" || request.method === "HEAD") {
      return html(loginPage({ next: safeNext(url.searchParams.get("next")) }));
    }
    if (request.method !== "POST") {
      return html(loginPage(), 405);
    }
    let form;
    try {
      form = await request.formData();
    } catch {
      return html(loginPage({ error: "הבקשה לא נקראה. נסה שוב." }), 400);
    }
    const target = safeNext(form.get("next"));
    // Both comparisons always run: `&&` would skip the password check
    // whenever the username is wrong, which times differently.
    const userOk = safeEqual(String(form.get("user") ?? ""), env.SITE_USER);
    const passOk = safeEqual(String(form.get("password") ?? ""), env.SITE_PASSWORD);
    if (!(userOk && passOk)) {
      // One message for both, so the form does not say which half was
      // right and turn itself into a username oracle.
      return html(
        loginPage({ next: target, error: "שם משתמש או סיסמה שגויים." }),
        401,
      );
    }
    const token = await issueSession(env, env.SITE_USER);
    if (!token) return unconfigured();
    return new Response(null, {
      status: 303,
      headers: {
        Location: target,
        "Set-Cookie": setCookie(token),
        "Cache-Control": "no-store",
      },
    });
  }

  // ---- everything else needs a session ----------------------------
  const claims = await verifySession(
    env,
    readCookie(request.headers.get("Cookie")),
  );
  if (!claims) {
    // An API caller gets a status it can act on; a browser gets the
    // form, with where it was going carried along so signing in lands
    // it there rather than on the front page.
    if (path.startsWith("/api/")) {
      return new Response(JSON.stringify({ error: "Not signed in." }), {
        status: 401,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store",
        },
      });
    }
    const target = `${path}${url.search}`;
    return new Response(null, {
      status: 303,
      headers: {
        Location: `/login?next=${encodeURIComponent(safeNext(target))}`,
        "Set-Cookie": clearCookie(),
        "Cache-Control": "no-store",
      },
    });
  }

  const response = await next();

  // Belt and braces: whatever the asset pipeline decided, a page behind
  // a login does not belong in a shared cache.
  const guarded = new Response(response.body, response);
  guarded.headers.set("Cache-Control", "private, no-store");
  guarded.headers.set("X-Content-Type-Options", "nosniff");
  guarded.headers.set("X-Frame-Options", "DENY");
  guarded.headers.set("Referrer-Policy", "same-origin");
  return guarded;
}
