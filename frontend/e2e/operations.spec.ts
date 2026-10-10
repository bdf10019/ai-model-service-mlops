import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { writeFile, mkdir } from 'node:fs/promises';
const password = process.env.E2E_PASSWORD!;
const guard = { 'X-Console-Request': '1', Origin: 'http://127.0.0.1:18120' };
test.beforeAll(async ({ request }) => { const r = await request.post('/api/bootstrap', { data: { username: 'e2eadmin', password }, headers: guard }); expect([201, 409]).toContain(r.status()); });
test.beforeEach(async ({ page }) => { await page.goto('/'); await page.getByLabel('帳號', { exact: true }).fill('e2eadmin'); await page.getByLabel('密碼', { exact: true }).fill(password); await page.getByRole('button', { name: '登入控制台' }).click(); await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible(); });
async function load(page: Page, provider: string, requests = 4, concurrency = 2) {
  await page.getByRole('button', { name: '負載與實驗追蹤', exact: true }).click();
  await page.getByLabel('負載模型').selectOption(provider);
  await page.getByLabel('負載請求數').fill(String(requests));
  await page.getByLabel('負載併發', { exact: true }).fill(String(concurrency));
  await page.getByLabel('負載預熱筆數').fill('1');
  await page.getByLabel('確認本機負載影響').check();
  const response = page.waitForResponse(r => r.url().endsWith('/api/loads') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '啟動有限負載測試' }).click();
  const r = await response; expect(r.status()).toBe(202);
  return (await r.json()).id as string;
}
async function waitLoad(page: Page, id: string) {
  await expect.poll(async () => (await (await page.request.get('/api/loads/' + id)).json()).status, { timeout: 90000 }).toMatch(/completed|stopped/);
  return (await (await page.request.get('/api/loads/' + id)).json());
}

test('監控鑽取、有限負載比較、安全停止及監控政策 Fixture 預覽', async ({ page }) => {
  const a = await waitLoad(page, await load(page, 'fixture'));
  expect(a.completed_requests).toBe(4); expect(a.rows[0].http_status).toBe(202);
  const b = await waitLoad(page, await load(page, 'fixture'));
  await page.getByLabel('負載比較基準').selectOption(a.id); await page.getByLabel('負載比較候選').selectOption(b.id);
  await page.getByRole('button', { name: '比較負載結果' }).click();
  await expect(page.getByText(/僅同 Provider/)).toBeVisible();
  await page.getByRole('button', { name: '鑽取負載筆數 1', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '任務詳情' })).toBeVisible();
  await page.getByLabel('關閉任務詳情').click();
  await page.screenshot({ path: '../reports/operations-load-fixture.png' });
  const stopped = await load(page, 'fixture', 20);
  await page.getByRole('button', { name: '安全停止負載測試' }).click();
  const result = await waitLoad(page, stopped); expect(result.status).toBe('stopped'); expect(result.completed_requests).toBeLessThan(20);
  await page.getByRole('button', { name: '監控與本機告警', exact: true }).click();
  await page.getByLabel('監控模型').selectOption('fixture');
  await page.getByLabel('監控圖表指標').selectOption('generation_ms_p95');
  await page.getByRole('button', { name: '檢視指標輸出（固定1小時）' }).click();
  await expect(page.getByText(/console_request_count/)).toBeVisible();
  await expect(page.getByRole('button', { name: a.rows[0].task_id.slice(0, 8), exact: true })).toBeVisible();
  await page.getByRole('button', { name: a.rows[0].task_id.slice(0, 8), exact: true }).click();
  await expect(page.getByRole('dialog', { name: '任務詳情' })).toBeVisible(); await page.getByLabel('關閉任務詳情').click();
  await page.screenshot({ path: '../reports/operations-monitoring-fixture.png' });
  await page.getByRole('button', { name: '客製化設定', exact: true }).click();
  await page.getByRole('button', { name: '驗證與預覽監控政策' }).click();
  await expect(page.getByText('受控預覽 · Fixture，不是實測')).toBeVisible();
  await page.getByRole('button', { name: '保存監控政策版本' }).click();
  await expect(page.getByText('監控政策新版本已保存，尚未套用。')).toBeVisible();
  await page.getByRole('button', { name: '確認套用監控政策' }).click();
  await expect(page.getByText('監控政策已明確套用；舊告警保留原政策版本。')).toBeVisible();
});

test('安全業務備份、分段驗證、影響確認與隔離還原', async ({ page }) => {
  await page.getByRole('button', { name: '備份與隔離還原', exact: true }).click();
  const downloaded = page.waitForEvent('download'); await page.getByRole('button', { name: '匯出安全業務備份' }).click();
  const file = await downloaded; const filePath = await file.path(); expect(filePath).toBeTruthy();
  await page.getByLabel('選擇備份檔').setInputFiles(filePath!);
  await expect(page.getByRole('heading', { name: '已驗證完整性與相容性，預覽還原影響' })).toBeVisible();
  await expect(page.getByRole('button', { name: '確認執行隔離還原' })).toBeDisabled();
  await page.getByLabel('確認隔離還原影響').check(); await page.getByRole('button', { name: '確認執行隔離還原' }).click();
  await expect(page.getByRole('heading', { name: '隔離還原已完成，未啟用正式服務' })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 }); await page.keyboard.press('Tab');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 2)).toBe(true);
});

test('真實凍結保留評測、有限 Q8 負載與 MLflow SQLite 重試去重', async ({ page }) => {
  test.skip(process.env.CONSOLE_REAL_MODEL !== '1', '真實模型明示未執行'); test.setTimeout(240000);
  const evidence: Record<string, unknown> = { scope: 'real_local_holdout_load_mlflow_no_container', stages: [] };
  try {
    await page.getByRole('button', { name: '模型與 Provider', exact: true }).click(); await page.getByRole('button', { name: '啟動候選引擎', exact: true }).click();
    await expect(page.getByText('真實引擎就緒', { exact: true })).toBeVisible({ timeout: 90000 }); await page.getByLabel('關閉任務詳情').click();
    await page.getByRole('button', { name: '客製化設定', exact: true }).click(); await page.getByRole('button', { name: '載入本輪建議設定' }).click();
    const saved = page.waitForResponse(r => r.url().endsWith('/api/configs') && r.request().method() === 'POST'); await page.getByRole('button', { name: '保存新版本', exact: true }).click();
    const config = await (await saved).json(); await page.getByRole('button', { name: '套用到沙盒候選', exact: true }).click();
    await page.getByRole('button', { name: '評測與版本比較', exact: true }).click(); await page.getByLabel('評測設定版本').selectOption(config.id); await page.getByLabel('固定評測集').selectOption('equipment-holdout-v1');
    const evaluated = page.waitForResponse(r => r.url().endsWith('/api/evaluations') && r.request().method() === 'POST'); await page.getByRole('button', { name: '執行正式評測' }).click();
    const reportId = (await (await evaluated).json()).id;
    await expect.poll(async () => (await (await page.request.get('/api/evaluations/' + reportId)).json()).status, { timeout: 90000 }).toBe('completed');
    const report = await (await page.request.get('/api/evaluations/' + reportId)).json(); expect(report.completed_cases).toBe(16); expect(report.metrics.generated_count).toBe(14);
    evidence.holdout = report; evidence.config = config;
    const a = await waitLoad(page, await load(page, 'q8', 6, 1)); evidence.load_A = a; expect(a.status).toBe('completed'); expect(a.rows.every((r: { model_sha256: string }) => r.model_sha256 === report.model_sha256)).toBe(true);
    const b = await waitLoad(page, await load(page, 'q8', 6, 2)); evidence.load_B = b; expect(b.status).toBe('completed');
    await page.getByLabel('負載比較基準').selectOption(a.id); await page.getByLabel('負載比較候選').selectOption(b.id); await page.getByRole('button', { name: '比較負載結果' }).click();
    await page.getByRole('button', { name: '模型與 Provider', exact: true }).click(); page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', { name: '停止引擎', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '任務詳情' }).locator('.badge.completed')).toBeVisible(); await page.getByLabel('關閉任務詳情').click();
    await page.getByRole('button', { name: '負載與實驗追蹤', exact: true }).click();
    for (const source of ['evaluations/' + reportId, 'loads/' + a.id]) {
      await page.getByLabel('同步真實報告').selectOption(source); await page.getByRole('button', { name: '同步／安全重試 MLflow' }).click();
      await expect.poll(async () => { const d = await (await page.request.get('/api/experiments')).json(); return d.sync.find((s: { source_id: string }) => s.source_id === source.split('/')[1])?.status; }, { timeout: 90000 }).toBe('synced');
      const before = await (await page.request.get('/api/experiments')).json(); await page.getByRole('button', { name: '同步／安全重試 MLflow' }).click(); const after = await (await page.request.get('/api/experiments')).json();
      expect(after.sync.find((s: { source_id: string }) => s.source_id === source.split('/')[1]).run_id).toBe(before.sync.find((s: { source_id: string }) => s.source_id === source.split('/')[1]).run_id);
    }
    evidence.experiments = await (await page.request.get('/api/experiments')).json();
    await page.getByRole('button', { name: '監控與本機告警', exact: true }).click(); evidence.monitoring = await (await page.request.get('/api/monitoring?provider=q8')).json();
    await expect(page.getByRole('button', { name: a.rows[0].task_id.slice(0, 8), exact: true })).toBeVisible();
    await page.screenshot({ path: '../reports/operations-monitoring.png' });
  } finally {
    await mkdir('../reports', { recursive: true }); await writeFile('../reports/operations-real.json', JSON.stringify(evidence, null, 2));
    const session = await (await page.request.get('/api/session')).json(); await page.request.post('/api/engine/stop', { data: {}, headers: { ...guard, 'X-CSRF-Token': session.csrf_token } });
  }
});
