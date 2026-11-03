// Next.js config. postgres is server only, kept out of the client bundle.
import type { NextConfig } from "next";

const config: NextConfig = {
  serverExternalPackages: ["postgres"],
};

export default config;
