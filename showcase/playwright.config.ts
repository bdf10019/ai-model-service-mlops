import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  outputDir: './reports/test-results',
  timeout: 30_000,
  expect: { timeout: 6_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['json', { outputFile: './reports/playwright-report.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:4178',
    browserName: 'chromium',
    channel: 'msedge',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'pnpm dev',
    url: 'http://127.0.0.1:4178',
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
