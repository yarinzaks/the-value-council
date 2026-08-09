// A password gate in front of every page of the published dashboard.
//
// Why this and not Cloudflare Access
// ----------------------------------
//
// Access is the better tool and its free tier covers 50 users, but
// enabling Zero Trust requires a payment method on file even at $0.
// This project is a personal paper-trading dashboard and the brief was
// "private, and it must never cost me anything", so the gate lives here
// instead: Pages Functions run on the free plan, need no subscription,
// and a `_middleware.js` at the functions root runs in front of static
// files as well as any route.
//
// What it is, honestly
// --------------------
//
// HTTP Basic authentication over TLS. That is weaker than Access: there
// is no second factor, no per-user audit, and no session revocation
// short of changing the password. It is appropriate for what is behind
// it — simulated portfolios, no real money, no personal data, no
// secrets — and it is a large improvement on a public URL that anyone
// who guesses the name can read.
//
// Setting the password
// --------------------
//
// In the Cloudflare dashboard: the Pages project -> Settings ->
// Variables and Secrets -> add two, for Production and Preview:
//
//     SITE_USER       whatever you like, e.g. yarin
//     SITE_PASSWORD   a long random one; encrypt it (choose "Secret")
//
// They are set there and only there. Neither appears in this
// repository, in the build, or in any log — the repository is public,
// so a password committed here would be a password published.

/**
 * Compare two strings without leaking their common prefix through
 * timing. `===` on strings returns as soon as it finds a difference,
 * which over enough requests reveals the password one character at a
 * time.
 */
function safeEqual(a, b) {
  const encoder = new TextEncoder();
  const left = encoder.encode(a);
  const right = encoder.encode(b);
  // Different lengths cannot be compared byte-for-byte, and the length
  // itself is not worth hiding here.
  if (left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) {
    diff |= left[i] ^ right[i];
  }
  return diff === 0;
}

/**
 * Parse `Authorization: Basic base64(user:pass)`. Null if malformed.
 *
 * The UTF-8 step is load-bearing and was missing at first, which broke
 * every password containing a character outside ASCII — Hebrew, an
 * accent, an emoji. Browsers encode the credentials as UTF-8 bytes and
 * then base64 them (we ask for exactly that with charset="UTF-8" in the
 * challenge), but `atob` hands back a *binary string*: one character
 * per byte, values 0-255. For ASCII that happens to equal the original.
 * For anything else "סיסמה" arrives as "×¡××¡××" and never matches.
 * Decoding the bytes properly is the fix.
 */
function readCredentials(header) {
  if (!header || !header.startsWith("Basic ")) return null;
  let decoded;
  try {
    const binary = atob(header.slice(6));
    const bytes = Uint8Array.from(binary, (ch) => ch.charCodeAt(0));
    decoded = new TextDecoder("utf-8").decode(bytes);
  } catch {
    // Not valid base64, or not valid UTF-8 — a probe, not a browser.
    return null;
  }
  const separator = decoded.indexOf(":");
  if (separator < 0) return null;
  return {
    user: decoded.slice(0, separator),
    password: decoded.slice(separator + 1),
  };
}

function unauthorized(message) {
  return new Response(message, {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="The Value Council", charset="UTF-8"',
      "Content-Type": "text/plain; charset=utf-8",
      // A 401 must never be cached, or a browser will keep replaying it
      // after the password is fixed.
      "Cache-Control": "no-store",
    },
  });
}

export async function onRequest(context) {
  const { request, env, next } = context;

  const expectedUser = env.SITE_USER;
  const expectedPassword = env.SITE_PASSWORD;

  // Fail closed. If the variables are missing — a fresh deploy, a typo
  // in the variable name, a preview branch nobody configured — the site
  // stays shut rather than quietly serving to everyone. The whole point
  // of this file is that there is no state in which it is open by
  // accident.
  if (!expectedUser || !expectedPassword) {
    return new Response(
      "This site is not configured yet. Set SITE_USER and SITE_PASSWORD " +
        "in the Pages project settings.",
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  const credentials = readCredentials(request.headers.get("Authorization"));
  if (!credentials) {
    return unauthorized("Authentication required.");
  }

  // Both comparisons always run: `&&` would skip the password check
  // whenever the username is wrong, which times differently.
  const userOk = safeEqual(credentials.user, expectedUser);
  const passwordOk = safeEqual(credentials.password, expectedPassword);
  if (!(userOk && passwordOk)) {
    return unauthorized("Incorrect username or password.");
  }

  const response = await next();

  // Belt and braces: whatever the asset pipeline decided, a page behind
  // a password should not be held in a shared cache.
  const guarded = new Response(response.body, response);
  guarded.headers.set("Cache-Control", "private, no-store");
  return guarded;
}
