import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["react-globe.gl", "three-globe"],
};

export default nextConfig;
