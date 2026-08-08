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
};

export default nextConfig;
