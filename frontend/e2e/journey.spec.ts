import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const password = process.env.E2E_PASSWORD!;
const admin = 'e2eadmin';
const headers = { 'X-Console-Request': '1', 'Origin': 'http://127.0.0.1:18120' };
test.beforeAll(async ({ request }) => {
  const r = await request.post('/api/bootstrap', { data: { username: admin, password }, headers });
  expect([201, 409]).toContain(r.status());
});
test.beforeEach(async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('帳號', { exact: true }).fill(admin);
  await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登入控制台' }).click();
  await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible();
});

test('真實登入、健康、雙設定與 Fixture 政策差異', async ({ page }) => {
  const initialTasks = (await (await page.request.get('/api/tasks')).json()).tasks as { profile_id: string }[];
  const previousServiceCount = initialTasks.filter(t => t.profile_id === 'service').length;
  await expect(page.getByText('產品資料庫')).toBeVisible();
  await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
  await page.getByLabel('Provider', { exact: true }).selectOption('fixture');
  await page.getByLabel('合成範例').selectOption('pump-medium-v1');
  const submitting = page.waitForResponse(r => r.url().endsWith('/api/inferences') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '執行並驗證' }).click();
  const serviceTaskId = (await (await submitting).json()).id as string;
  const drawer = page.getByRole('dialog', { name: '任務詳情' });
  await expect(drawer.getByText('本次規則未要求額外複核', { exact: false })).toBeVisible();
  await expect(drawer.getByText('service-v1 / triage-service-v1')).toBeVisible();
  await expect(drawer.getByText('原文證據', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByLabel('目前設定', { exact: true }).selectOption('asset');
  await page.getByRole('button', { name: '執行並驗證' }).click();
  await expect(drawer.getByText('需要人工複核 · 不可直接發布或執行設備動作')).toBeVisible();
  await expect(drawer.getByText('asset-v1 / triage-asset-v1')).toBeVisible();
  await expect(drawer.getByText('Fixture · 非模型成績')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(drawer).not.toBeVisible();
  await page.getByRole('button', { name: '任務與佇列', exact: true }).click();
  await page.getByLabel('任務設定篩選').selectOption('service');
  await expect(page.locator('tbody tr')).toHaveCount(previousServiceCount + 1);
  await expect(page.locator('tbody tr').filter({ hasText: 'asset-v1' })).toHaveCount(0);
  const ownTaskRow = page.locator('tbody tr').filter({ hasText: serviceTaskId.slice(0, 8) });
  await expect(ownTaskRow).toHaveCount(1);
  await ownTaskRow.getByRole('button', { name: '詳情' }).click();
  await expect(drawer.getByText('service-v1 / triage-service-v1')).toBeVisible();
});

test('表單必填與安全風險強制複核', async ({ page }) => {
  await page.getByLabel('目前設定', { exact: true }).selectOption('asset');
  await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
  await page.getByLabel('Provider', { exact: true }).selectOption('fixture');
  await page.getByLabel('事件文字', { exact: true }).fill('泵浦在特定負載下反覆出現震動，尚未提供量測值。');
  await page.getByRole('button', { name: '執行並驗證' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page.getByLabel('設備重要性')).toBeFocused();
  await page.getByLabel('合成範例').selectOption('motor-critical-v1');
  await page.getByRole('button', { name: '執行並驗證' }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('風險：危急')).toBeVisible();
  await expect(drawer.getByText('停止使用並通知負責人', { exact: true })).toBeVisible();
});

test('一般使用者登入與 API 越權拒絕', async ({ page, request }) => {
  await page.getByRole('button', { name: /帳號與角色/ }).click();
  await page.getByLabel('新帳號').fill('e2eoperator');
  await page.getByLabel('新密碼').fill(password);
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  await expect(page.getByText('帳號已建立。請使用該帳號登入驗證角色。')).toBeVisible();
  await page.getByRole('button', { name: '登出', exact: true }).click();
  await page.getByLabel('帳號', { exact: true }).fill('e2eoperator');
  await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登入控制台' }).click();
  await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  await expect(page.getByRole('button', { name: '啟動候選引擎', exact: true })).not.toBeVisible();
  const login = await request.post('/api/sessions', { data: { username: 'e2eoperator', password }, headers });
  const csrf = (await login.json()).csrf_token;
  expect((await request.post('/api/engine/start', { data: {}, headers: { ...headers, 'X-CSRF-Token': csrf, 'X-Role': 'admin' } })).status()).toBe(403);
});

test('鍵盤導覽與手機版面', async ({ page }) => {
  await page.getByRole('button', { name: '推論 Playground', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: '推論 Playground', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: '切換導覽' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.getByRole('button', { name: '切換導覽' }).click();
  await page.getByRole('button', { name: '客製化設定', exact: true }).click();
  await expect(page.getByRole('heading', { name: '同一事件，實際差異' })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole('button', { name: '服務總覽', exact: true }).click();
  await page.screenshot({ path: '../reports/console-overview.png', fullPage: true });
});

test('網路失敗顯示恢復資訊', async ({ page }) => {
  await page.route('**/api/health', route => route.abort());
  await page.getByRole('button', { name: '更新狀態', exact: false }).click();
  await expect(page.getByText('連線失敗，以下保留最後取得的資料。請重新連線再判斷健康狀態。')).toBeVisible();
});

test('UI 啟動真實 Q8、推論與停止自己的引擎', async ({ page }) => {
  test.skip(process.env.CONSOLE_REAL_MODEL !== '1', '需要已核對的本機模型與 GPU；CI 不下載權重。');
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  await page.getByRole('button', { name: '啟動候選引擎', exact: true }).click();
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('已完成', { exact: true }).first()).toBeVisible({ timeout: 90000 });
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByRole('button', { name: '更新狀態', exact: false }).click();
  await expect(page.getByText('真實引擎就緒', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
  const cases: Record<string, unknown>[] = [];
  for (const profileId of ['service', 'asset']) {
    await page.getByLabel('目前設定', { exact: true }).selectOption(profileId);
    for (const sampleId of ['pump-medium-v1', 'motor-critical-v1', 'input-injection-v1']) {
      await page.getByLabel('合成範例').selectOption(sampleId);
      const submitted = page.waitForResponse(r => r.url().endsWith('/api/inferences') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '執行並驗證' }).click();
      const taskId = (await (await submitted).json()).id;
      await expect(drawer.getByText('原文證據', { exact: true })).toBeVisible({ timeout: 70000 });
      await expect(drawer.getByText('真實本機模型', { exact: true })).toBeVisible();
      const task = await (await page.request.get('/api/tasks/' + taskId)).json();
      const resource = await (await page.request.get('/api/health')).json();
      cases.push({ task_id: task.id, trace_id: task.trace_id, sample_id: sampleId, profile_version: task.profile_version, config_snapshot_sha256: task.config_snapshot_sha256, model_sha256: task.model_sha256, model_version: task.model_version, status: task.status, queue_ms: task.queue_ms, generation_ms: task.generation_ms, elapsed_ms: task.elapsed_ms, raw_schema_valid: task.raw_schema_valid, warnings: task.warnings, raw_output: task.raw_output, result: task.result, generation: task.generation, resource: { ram_free_gib: resource.ram_free_gib, gpu: resource.gpu, sampled_at: resource.sampled_at } });
      if (profileId === 'asset' && sampleId === 'pump-medium-v1') {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.screenshot({ path: '../reports/real-inference.png', fullPage: false });
      }
      await page.getByRole('button', { name: '關閉任務詳情' }).click();
    }
  }
  await mkdir('../reports', { recursive: true });
  await writeFile('../reports/serial-smoke-baseline.json', JSON.stringify({ schema_version: '1.0', evidence_kind: 'measured', method: 'six_serial_UI_smoke_requests_not_load_or_quality_evaluation', concurrency: 1, max_tokens: 256, context_tokens: 4096, temperature: 0, seed: 42, engine_build: 'b10665', token_level_ttft: null, cases }, null, 2));
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: '停止引擎', exact: true }).click();
  await expect(drawer.getByText('已完成', { exact: true }).first()).toBeVisible();
});
