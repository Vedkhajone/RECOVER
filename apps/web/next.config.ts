import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Next 16 blocks cross-origin requests for dev assets by default. The e2e
  // suite drives the app over 127.0.0.1 while the dev server binds localhost,
  // which trips that guard and leaves the page without its JS chunks.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // The API base is read at runtime in the browser too, so it is exposed
  // explicitly rather than inlined. Only the public base URL - never a secret.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },
};

export default nextConfig;
