const nextJest = require('next/jest')

const createJestConfig = nextJest({
  dir: './',
})

const customJestConfig = {
  testEnvironment: 'node',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
  },
  testMatch: ['<rootDir>/tests/**/*.test.ts'],
  testPathIgnorePatterns: ['<rootDir>/.next/', '<rootDir>/node_modules/'],
  // Every suite talks to the same Postgres, so parallel workers would fight
  // over fixture rows.
  maxWorkers: 1,
  testTimeout: 30000,
}

module.exports = createJestConfig(customJestConfig)
