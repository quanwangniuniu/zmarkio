/** @type {import('next').NextConfig} */
const nextConfig = {
  // Lint is enforced separately via `npm run lint` in CI.
  // Build-time lint is skipped because Storybook .stories.tsx files
  // trigger react-hooks/rules-of-hooks false positives.
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    // TSC errors are now 0 — enforce type checking at build time
    ignoreBuildErrors: false,
  },
  // The MSW worker lives in public/msw (workerDirectory), so its script URL
  // is not at the root; allow it to control the whole origin when the mock
  // switch (NEXT_PUBLIC_API_MOCKING=on) registers it with scope '/'.
  async headers() {
    return [
      {
        source: '/msw/mockServiceWorker.js',
        headers: [{ key: 'Service-Worker-Allowed', value: '/' }],
      },
    ];
  },
  webpack: (config, { isServer, webpack }) => {
    // Ignore pino-pretty during bundling to prevent build errors
    // pino-pretty is a Node.js-only package that shouldn't be bundled
    config.plugins.push(
      new webpack.IgnorePlugin({
        resourceRegExp: /^pino-pretty$/,
      })
    );
    
    // Also mark as external for server-side bundling
    if (isServer) {
      config.externals = config.externals || [];
      if (Array.isArray(config.externals)) {
        config.externals.push('pino-pretty');
      }
    }
    
    return config;
  },
};

export default nextConfig;
