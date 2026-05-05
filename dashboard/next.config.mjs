/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The dashboard reads files from ../data/ at runtime via Node fs.
  // No image optimization or remote fetching is required.
  experimental: {
    serverComponentsExternalPackages: [],
  },
};

export default nextConfig;
