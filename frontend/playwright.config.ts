import { defineConfig, devices } from '@playwright/test';
import { config as loadEnv } from 'dotenv';

// Load .env.local for local dev credentials (gitignored; CI uses env vars directly).
loadEnv({ path: '.env.local', override: false });

const baseURL = process.env.BASE_URL || 'http://localhost';
const webServerURL = process.env.PLAYWRIGHT_WEB_SERVER_URL || baseURL;

/** When nginx/Docker already serves BASE_URL, set E2E_USE_EXISTING_SERVER=1 to skip `npm run dev`. */
const useExistingServer =
  process.env.E2E_USE_EXISTING_SERVER === '1' ||
  process.env.E2E_USE_EXISTING_SERVER === 'true';

/**
 * See https://playwright.dev/docs/test-configuration.
 */
export default defineConfig({
  testDir: './e2e',
  ...(useExistingServer
    ? {}
    : {
        /* Start Next.js when no external stack is running. */
        webServer: {
          command: 'npm run dev',
          url: webServerURL,
          reuseExistingServer: !process.env.CI,
          timeout: 60_000,
        },
      }),
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry tests 1 time */
  retries: 1,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions. */
  use: {
    /* Base URL to use in actions like `await page.goto('')`. Next.js dev runs on 3000. */
    baseURL,
    launchOptions: process.env.PLAYWRIGHT_HOST_RESOLVER_RULES
      ? { args: [`--host-resolver-rules=${process.env.PLAYWRIGHT_HOST_RESOLVER_RULES}`] }
      : undefined,

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    
  },

  /* Configure projects */
  projects: [
    /* Global auth setup (real login) – runs first; produces e2e/.auth/user.json for decisions, tasks, spreadsheets */
    { name: 'setup', testMatch: /e2e[\\/]auth\.setup\.ts$/ },
    /* Auth-folder setup (mocked login) – runs when testing e2e/auth */
    { name: 'auth-setup', testMatch: /e2e[\\/]auth[\\/]auth\.setup\.ts$/ },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: /e2e[\\/]auth[\\/]/,
    },
    {
      name: 'firefox',
      use: {
        ...devices['Desktop Firefox'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: /e2e[\\/]auth[\\/]/,
    },
    {
      name: 'webkit',
      use: {
        ...devices['Desktop Safari'],
        storageState: 'e2e/.auth/user.json',
      },
      dependencies: ['setup'],
      testIgnore: /e2e[\\/]auth[\\/]/,
    },
    {
      name: 'auth-chromium',
      use: {
        ...devices['Desktop Chrome'],
        storageState: 'e2e/auth/.auth/user.json',
      },
      dependencies: ['auth-setup'],
      testMatch: /e2e[\\/]auth[\\/]/,
      testIgnore: [/\.setup\.ts$/, /[\\/]fixtures[\\/]/],
    },
    {
      name: 'auth-firefox',
      use: {
        ...devices['Desktop Firefox'],
        storageState: 'e2e/auth/.auth/user.json',
      },
      dependencies: ['auth-setup'],
      testMatch: /e2e[\\/]auth[\\/]/,
      testIgnore: [/\.setup\.ts$/, /[\\/]fixtures[\\/]/],
    },
    {
      name: 'auth-webkit',
      use: {
        ...devices['Desktop Safari'],
        storageState: 'e2e/auth/.auth/user.json',
      },
      dependencies: ['auth-setup'],
      testMatch: /e2e[\\/]auth[\\/]/,
      testIgnore: [/\.setup\.ts$/, /[\\/]fixtures[\\/]/],
    },
    {
      name: 'messages-mock',
      use: {
        ...devices['Desktop Chrome'],
      },
      testMatch: [
        /e2e[\\/]messages[\\/]messages-reconnect-attachments\.spec\.ts$/,
        /e2e[\\/]messages[\\/]messages-pinned\.spec\.ts$/,
        /e2e[\\/]messages[\\/]messages-link-preview\.spec\.ts$/,
        /e2e[\\/]messages[\\/]messages-ordering-jitter\.spec\.ts$/,
      ],
    },
    {
      /* Load/perf specs: need the running Docker stack (and the load fixture
         for these two). Never wired into CI; run explicitly with
         --project=messages-load and E2E_USE_EXISTING_SERVER=1. */
      name: 'messages-load',
      use: {
        ...devices['Desktop Chrome'],
      },
      testMatch: [
        /e2e[\\/]messages[\\/]messages-burst-perf\.spec\.ts$/,
        /e2e[\\/]messages[\\/]messages-live-load\.spec\.ts$/,
        /e2e[\\/]messages[\\/]messages-multi-client-render\.spec\.ts$/,
      ],
    },
    {
      name: 'budget-mock',
      use: {
        ...devices['Desktop Chrome'],
      },
      testMatch: /e2e[\\/]budget[\\/]budget-admin-override\.spec\.ts$/,
    },
  ],
});
