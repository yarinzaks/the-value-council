/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `next build` and `next dev` share .next, so verifying a build while
  // someone has the dashboard open wipes the dev server's cache out from
  // under them. Setting NEXT_DIST_DIR sends the build somewhere else;
  // unset, nothing changes. One catch: `next build` rewrites
  // tsconfig.json to reference whichever dist dir it used, so check that
  // file out again after a verification build instead of committing it.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The dashboard reads files from ../data/ at runtime via Node fs.
  // No image optimization or remote fetching is required.
  experimental: {
    serverComponentsExternalPackages: [],
  },
  // Static language build. Set NEXT_PUBLIC_SITE_LOCALE to "en" or "he"
  // and this becomes a pure static export rooted at /en or /he — the
  // two are built separately and published side by side, which is how
  // the site keeps server-rendered Hebrew without a server to render
  // it. Unset (the default, and what `next dev` sees) nothing here
  // applies and the app behaves exactly as before.
  //
  // The fs reads in lib/data.ts then happen once, at build time, on a
  // machine that has the data tree. What ships is HTML.
  ...(process.env.NEXT_PUBLIC_SITE_LOCALE
    ? {
        output: "export",
        basePath: `/${process.env.NEXT_PUBLIC_SITE_LOCALE}`,
        // Emits `agents/index.html` rather than `agents.html`, which is
        // what a static host resolves for `/en/agents` without needing
        // per-host rewrite rules.
        trailingSlash: true,
      }
    : {}),
};

export default nextConfig;
