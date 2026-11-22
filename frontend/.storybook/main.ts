import type { StorybookConfig } from '@storybook/nextjs';
import path from 'path';

const config: StorybookConfig = {
  stories: ['../src/**/*.stories.@(js|jsx|ts|tsx|mdx)'],
  addons: [
    '@storybook/addon-links',
    '@storybook/addon-essentials',
    '@storybook/addon-interactions',
    '@storybook/addon-a11y',
  ],
  framework: {
    name: '@storybook/nextjs',
    options: {},
  },
  docs: {
    autodocs: 'tag',
  },
  staticDirs: ['../public'],
  webpackFinal: async (config) => {
    if (config.resolve) {
      // Strip `@` from the merged alias so it can be appended last. Otherwise the key
      // order from Next's preset keeps `@` first; AliasPlugin then matches `@` before
      // `@/lib/api/...` and loads the real API instead of Storybook mocks.
      const rawAlias = config.resolve.alias;
      const base =
        rawAlias && typeof rawAlias === 'object' && !Array.isArray(rawAlias)
          ? { ...(rawAlias as Record<string, string>) }
          : {};
      const { '@': _omitAt, ...restAlias } = base;

      config.resolve.alias = {
        ...restAlias,
        '@/lib/api/facebookMetaPhotoApi': path.resolve(
          __dirname,
          '../src/stories/facebook-meta/mocks/facebookMetaPhotoApi.mock.ts'
        ),
        '@/lib/api/facebookMetaVideoApi': path.resolve(
          __dirname,
          '../src/stories/facebook-meta/mocks/facebookMetaVideoApi.mock.ts'
        ),
        'next/navigation': path.resolve(
          __dirname,
          '../src/stories/mocks/nextNavigation.mock.ts'
        ),
        // Let `@` resolve `@/components/ui/Modal` → src/components/ui/Modal.js (the old
        // StorybookModal.tsx path no longer exists and broke all stories that import Modal).
        '@': path.resolve(__dirname, '../src'),
      };
    }
    return config;
  },
};

export default config;

