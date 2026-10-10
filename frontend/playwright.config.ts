import { defineConfig } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
process.env.E2E_PASSWORD ||= randomUUID() + randomUUID();
process.env.CONSOLE_STATE_DIR ||= 'tmp/e2e-' + Date.now();
export default defineConfig({
  testDir: './e2e', timeout: 120000, workers: 1, fullyParallel: false,
  use: { actionTimeout: 15000, baseURL: 'http://127.0.0.1:18120', channel: 'msedge', headless: true, trace: 'off', screenshot: 'only-on-failure' },
  outputDir: '../reports/e2e-artifacts',
  reporter: [['list'], ['json', { outputFile: '../reports/e2e-results.json' }]],
  webServer: {
    command: '"' + resolve('../.venv/Scripts/python.exe') + '" -m uvicorn console_app.app:factory --factory --host 127.0.0.1 --port 18120 --no-access-log',
    url: 'http://127.0.0.1:18120/api/setup', reuseExistingServer: false, timeout: 30000,
    env: { CONSOLE_STATE_DIR: process.env.CONSOLE_STATE_DIR, CONSOLE_ENGINE_PORT: '18121', PYTHONUTF8: '1' },
  },
});
