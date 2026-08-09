import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The sandbox's subprocess stdio drops the TypeScript CLI's --showConfig
  // output. Keep Next's compiler-API checker for deterministic builds here.
  experimental: {
    useTypeScriptCli: false,
  },
  async redirects() {
    return [
      {
        source: "/scanner",
        destination: "/scanners/minervini-vcp",
        permanent: true,
      },
      {
        source: "/scanner/:path*",
        destination: "/scanners/minervini-vcp",
        permanent: true,
      },
      {
        source: "/scanners/minervini",
        destination: "/scanners/minervini-vcp",
        permanent: true,
      },
    ]
  },
};

export default nextConfig;
