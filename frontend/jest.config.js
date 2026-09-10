const nextJest = require('next/jest')

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files
  dir: './',
})

// Add any custom config to be passed to Jest
const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
  },
  testMatch: [
    '<rootDir>/src/**/__tests__/**/*.{js,jsx,ts,tsx}',
    '<rootDir>/src/**/*.{test,spec}.{js,jsx,ts,tsx}'
  ],
  // Coverage collection is intentionally limited to shared lib + ui primitives:
  // - src/lib/** includes API clients (src/lib/api/**), stores, and helpers
  // - src/components/ui/** covers reusable UI building blocks
  // Feature pages/components stay out of the global floor so the gate stays
  // meaningful without requiring full-app coverage in one step. Verified via
  // coverage/coverage-summary.json: collected files are only under these globs.
  // Exclude stories, barrels, configs, and local mock fixtures (not production).
  collectCoverageFrom: [
    'src/lib/**/*.{js,jsx,ts,tsx}',
    'src/components/ui/**/*.{js,jsx,ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/*.stories.{js,jsx,ts,tsx}',
    '!src/**/index.{js,jsx,ts,tsx}',
    '!src/**/*.config.{js,jsx,ts,tsx}',
    '!src/lib/mock/**',
    '!src/**/jest.setup.js',
    '!src/**/jest.config.js'
  ],
  // Global floor for the collected scope above. Higher floors apply only to
  // "critical" modules (auth / permission / open-redirect hardening / approver
  // UI) where regressions are high-risk. Thresholds are set below current
  // measured coverage so they catch drops without blocking normal iteration.
  // Broader api/ trees stay on the global floor until more backfill lands.
  coverageThreshold: {
    global: {
      branches: 30,
      functions: 30,
      lines: 30,
      statements: 30
    },
    // Auth session restore / token refresh for the public portal.
    './src/lib/portalAuth.ts': {
      branches: 70,
      functions: 90,
      lines: 80,
      statements: 80
    },
    // Chat permission checks (who can read/write/manage).
    './src/lib/chatPermissions.ts': {
      branches: 70,
      functions: 90,
      lines: 90,
      statements: 90
    },
    // Safe internal "from" return paths (open-redirect hardening).
    './src/lib/notificationsNavigation.ts': {
      branches: 80,
      functions: 90,
      lines: 90,
      statements: 90
    },
    // Approver assignment API used by access-control settings.
    './src/lib/api/approverApi.ts': {
      branches: 90,
      functions: 90,
      lines: 90,
      statements: 90
    },
    // Admin override audit trail client.
    './src/lib/api/adminOverrideAuditApi.ts': {
      branches: 90,
      functions: 90,
      lines: 90,
      statements: 85
    },
    // Permission matrix UI for role × capability editing.
    './src/components/ui/PermissionMatrix.tsx': {
      branches: 50,
      functions: 65,
      lines: 70,
      statements: 70
    },
    // Approver multi-select used across approval workflows.
    './src/components/ui/ApproverSelect.tsx': {
      branches: 70,
      functions: 60,
      lines: 75,
      statements: 70
    }
  },
  testPathIgnorePatterns: [
    '<rootDir>/.next/',
    '<rootDir>/node_modules/',
    '<rootDir>/coverage/',
    '<rootDir>/dist/',
    '/__mocks__/'
  ],
  transformIgnorePatterns: [
    '/node_modules/',
    '^.+\\.module\\.(css|sass|scss)$',
  ],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json', 'node'],
  globals: {
    'ts-jest': {
      tsconfig: '<rootDir>/tsconfig.json',
    },
  },
}

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async
module.exports = createJestConfig(customJestConfig)
