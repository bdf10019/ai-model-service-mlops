import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const password = process.env.E2E_PASSWORD!;
const guard = { 'X-Console-Request': '1', Origin: 'http://127.0.0.1:18120' };
const measured: Record<string, unknown>[] = [];

test.beforeAll(async ({ request }) => {
  const response = await request.post('/api/bootstrap', { data: { username: 'e2eadmin', password }, headers: guard });
  expect([201, 409]).toContain(response.status());
});
async function login(page: Page, name = 'e2eadmin') {
  await page.goto('/');
  await page.getByLabel('帳號', { exact: true }).fill(name);
  await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登入控制台' }).click();
  await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible();
}
test.beforeEach(async ({ page }) => login(page));

async function saveConfig(page: Page, tokens: number, seed: number, recommended = false) {
  await page.getByRole('button', { name: '客製化設定', exact: true }).click();
  await expect(page.getByRole('heading', { name: '編輯、預覽與保存' })).toBeVisible();
  if (recommended) await page.getByRole('button', { name: '載入本輪建議設定' }).click();
  await page.getByLabel('Token 上限').fill(String(tokens));
  await page.getByLabel('隨機種子').fill(String(seed));
  await page.getByRole('button', { name: '驗證與預覽', exact: true }).click();
  await expect(page.getByRole('heading', { name: '同一合成案例預覽 · Fixture' })).toBeVisible();
  const response = page.waitForResponse(r => r.url().endsWith('/api/configs') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '保存新版本', exact: true }).click();
  const version = (await (await response).json()).id;
  await expect(page.getByText('新設定版本已保存；線上版本及既有任務保持原快照。')).toBeVisible();
  await page.getByRole('button', { name: '套用到沙盒候選', exact: true }).click();
  await expect(page.getByText('已套用至沙盒候選；正式服務仍使用已發布版本。')).toBeVisible();
  return version as string;
}
async function evaluate(page: Page, version: string, provider = 'fixture') {
  await page.getByRole('button', { name: '評測與版本比較', exact: true }).click();
  await page.getByLabel('評測設定版本').selectOption(version);
  await page.getByLabel('評測模型').selectOption(provider);
  const response = page.waitForResponse(r => r.url().endsWith('/api/evaluations') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '執行正式評測', exact: true }).click();
  const reportId = (await (await response).json()).id;
  await expect.poll(async () => (await (await page.request.get('/api/evaluations/' + reportId)).json()).status, { timeout: 100000 }).toBe('completed');
  await expect(page.getByLabel('模型與安全層品質圖表')).toBeVisible();
  const report = await (await page.request.get('/api/evaluations/' + reportId)).json();
  expect(report.completed_cases).toBe(24);
  expect(report.metrics.generated_count).toBe(20);
  return report;
}

test('設定預覽保存、固定評測、鑽取、比較與 Fixture 发布拒絕', async ({ page }) => {
  const versionA = await saveConfig(page, 320, 42);
  const a = await evaluate(page, versionA);
  await page.getByLabel('案例篩選').selectOption('invalid');
  const explorer = page.getByRole('heading', { name: '逐筆結果與失敗案例' }).locator('..').locator('..');
  await expect(explorer.locator('tbody tr')).toHaveCount(4);
  await explorer.getByRole('button', { name: 'f01 · format_error' }).click();
  await expect(page.getByLabel('案例證據')).toContainText('input_rejected_correctly');
  const versionB = await saveConfig(page, 384, 84);
  const b = await evaluate(page, versionB);
  await page.getByLabel('比較基準').selectOption(a.id);
  await page.getByLabel('比較候選').selectOption(b.id);
  await page.getByRole('button', { name: '比較執行與設定差異' }).click();
  await expect(page.getByText('同一評測／程式／Provider 契約，可比較設定與版本差異。')).toBeVisible();
  await expect(page.getByText('max_tokens', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
  await page.getByLabel('待檢查評測').selectOption(b.id);
  await page.getByRole('button', { name: '檢查發布資格' }).click();
  await expect(page.getByRole('heading', { name: '發布閘門拒絕' })).toBeVisible();
  await expect(page.getByText('Fixture 不得發布', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '建立發布提案' })).toBeDisabled();
});

test('設定匯入驗證、還原不改寫歷史與手機表單', async ({ page }) => {
  await page.getByRole('button', { name: '客製化設定', exact: true }).click();
  await page.getByLabel('匯入設定').setInputFiles({ name: 'bad.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ id: 'service', secret: 'SYNTHETIC_SECRET_VALUE' })) });
  await expect(page.getByRole('alert')).toContainText('欄位格式不符');
  await expect(page.getByRole('alert')).not.toContainText('SYNTHETIC_SECRET_VALUE');
  const old = await page.getByLabel('編輯設定版本').inputValue();
  await page.getByRole('button', { name: '還原預設為新版本' }).click();
  await expect(page.getByText('預設已還原為新版本；歷史與正式服務未改寫。')).toBeVisible();
  expect(await page.getByLabel('編輯設定版本').inputValue()).not.toBe(old);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.getByLabel('產品名稱').focus(); await page.keyboard.press('Tab');
  await expect(page.getByLabel('操作術語')).toBeFocused();
});

test('真實 Q8 固定評測、獨立核准、雙版本發布、推論與回滾', async ({ page }) => {
  test.skip(process.env.CONSOLE_REAL_MODEL !== '1', '需要已核對的本機模型與 GPU；不下載權重。');
  test.setTimeout(360000);
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  await page.getByRole('button', { name: '啟動候選引擎' }).click();
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true }).first()).toBeVisible({ timeout: 90000 });
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByRole('button', { name: '更新狀態', exact: false }).click();
  await expect(page.getByText('真實引擎就緒', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /帳號與角色/ }).click();
  await page.getByLabel('新帳號').fill('pipelinereview'); await page.getByLabel('新密碼').fill(password);
  await page.getByLabel('角色', { exact: true }).selectOption('reviewer');
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  await expect(page.getByText('帳號已建立。請使用該帳號登入驗證角色。')).toBeVisible();

  const baseline = await evaluate(page, 'service-v1', 'q8'); measured.push({ stage: 'fixed_v1_baseline', report: baseline });
  const versionA = await saveConfig(page, 320, 42, true);
  const a = await evaluate(page, versionA, 'q8'); measured.push({ stage: 'candidate_A_evaluation', report: a });
  async function proposeAndApprove(report: any) {
    await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
    await page.getByLabel('待檢查評測').selectOption(report.id);
    await page.getByRole('button', { name: '檢查發布資格' }).click();
    await expect(page.getByRole('heading', { name: '發布閘門通過' })).toBeVisible();
    const response = page.waitForResponse(r => r.url().endsWith('/api/releases') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '建立發布提案' }).click();
    const release = await (await response).json();
    await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, 'pipelinereview');
    await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
    const row = page.locator('tbody tr').filter({ has: page.getByRole('button', { name: release.config_version, exact: true }) });
    await row.getByRole('button', { name: '核准', exact: true }).click();
    await expect(page.getByText('獨立核准已保存並綁定完整版本。')).toBeVisible();
    await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page);
    await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
    await page.locator('tbody tr').filter({ has: page.getByRole('button', { name: release.config_version, exact: true }) }).getByRole('button', { name: '套用版本' }).click();
    return release;
  }
  async function runSwitch(action: '發布' | '回滾') {
    await page.getByLabel('確認版本切換影響').check();
    const response = page.waitForResponse(r => r.url().endsWith(action === '發布' ? '/publish' : '/rollback') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '確認並執行' + action }).click();
    const taskId = (await (await response).json()).id;
    await expect.poll(async () => (await (await page.request.get('/api/tasks/' + taskId)).json()).status, { timeout: 90000 }).toBe('completed');
    await expect(page.getByText('版本操作已完成，真實模型與設定驗證證據已保存。')).toBeVisible();
    measured.push({ stage: action, task: await (await page.request.get('/api/tasks/' + taskId)).json() });
  }
  async function infer(release: any, tokens: number, seed: number, stage: string) {
    await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
    await page.getByLabel('合成範例').selectOption('pump-medium-v1');
    await page.getByLabel('執行版本').selectOption('auto');
    const response = page.waitForResponse(r => r.url().endsWith('/api/inferences') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '執行並驗證' }).click();
    const id = (await (await response).json()).id;
    await expect(page.getByRole('dialog').getByText('原文證據', { exact: true })).toBeVisible({ timeout: 70000 });
    const task = await (await page.request.get('/api/tasks/' + id)).json();
    expect(task.release_id).toBe(release.id); expect(task.profile_version).toBe(release.config_version);
    expect(task.config_snapshot.max_tokens).toBe(tokens); expect(task.config_snapshot.seed).toBe(seed);
    expect(task.generation.max_tokens).toBe(tokens); expect(task.generation.seed).toBe(seed);
    expect(task.model_sha256).toBe(release.model_sha256); measured.push({ stage, task });
    await page.getByRole('button', { name: '關閉任務詳情' }).click();
  }
  const releaseA = await proposeAndApprove(a); await runSwitch('發布'); await infer(releaseA, 320, 42, 'A_actual_inference');
  const versionB = await saveConfig(page, 384, 84, true);
  const b = await evaluate(page, versionB, 'q8'); measured.push({ stage: 'candidate_B_evaluation', report: b });
  await page.getByLabel('比較基準').selectOption(a.id); await page.getByLabel('比較候選').selectOption(b.id);
  await page.getByRole('button', { name: '比較執行與設定差異' }).click();
  await expect(page.getByText('max_tokens', { exact: true })).toBeVisible();
  await page.getByLabel('模型與安全層品質圖表').scrollIntoViewIfNeeded(); await page.screenshot({ path: '../reports/formal-evaluation.png', fullPage: false });
  const releaseB = await proposeAndApprove(b); await runSwitch('發布'); await infer(releaseB, 384, 84, 'B_actual_inference');
  await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
  await expect(page.getByRole('button', { name: '回滾上一個可用版本' })).toBeEnabled();
  await page.getByRole('button', { name: '回滾上一個可用版本' }).click(); await runSwitch('回滾');
  await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({ path: '../reports/verified-rollback.png', fullPage: false });
  await infer(releaseA, 320, 42, 'rollback_A_actual_inference');
  const releases = await (await page.request.get('/api/releases')).json();
  expect(releases.active.service).toBe(releaseA.id); expect(releases.previous.service).toBe(releaseB.id);
  measured.push({ stage: 'final_release_state', releases });
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', { name: '停止引擎' }).click();
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true }).first()).toBeVisible();
});
test('設備設定真實評測、雙核准與維護時段後發布', async ({ page }) => {
  test.skip(process.env.CONSOLE_REAL_MODEL !== '1', '需要已核對的本機模型與 GPU；不下載權重。');
  test.setTimeout(180000);
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  await page.getByRole('button', { name: '啟動候選引擎' }).click();
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true }).first()).toBeVisible({ timeout: 90000 });
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByRole('button', { name: '更新狀態', exact: false }).click();
  await page.getByRole('button', { name: /帳號與角色/ }).click();
  await page.getByLabel('新帳號').fill('pipelinereviewtwo'); await page.getByLabel('新密碼').fill(password);
  await page.getByLabel('角色', { exact: true }).selectOption('reviewer');
  await page.getByRole('button', { name: '建立帳號', exact: true }).click();
  await expect(page.getByText('帳號已建立。請使用該帳號登入驗證角色。')).toBeVisible();
  await page.getByLabel('目前設定', { exact: true }).selectOption('asset');
  const version = await saveConfig(page, 384, 84, true);
  const report = await evaluate(page, version, 'q8'); measured.push({ stage: 'asset_actual_evaluation', report });
  await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
  await page.getByLabel('待檢查評測').selectOption(report.id);
  await page.getByRole('button', { name: '檢查發布資格' }).click();
  await expect(page.getByRole('heading', { name: '發布閘門通過' })).toBeVisible();
  const proposed = page.waitForResponse(r => r.url().endsWith('/api/releases') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '建立發布提案' }).click();
  const release = await (await proposed).json(); expect(release.approvals_required).toBe(2);
  for (const name of ['pipelinereview', 'pipelinereviewtwo']) {
    await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page, name);
    await page.getByLabel('目前設定', { exact: true }).selectOption('asset');
    await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
    const row = page.locator('tbody tr').filter({ has: page.getByRole('button', { name: version, exact: true }) });
    await row.getByRole('button', { name: '核准', exact: true }).click();
    await expect(page.getByText('獨立核准已保存並綁定完整版本。')).toBeVisible();
    if (name === 'pipelinereview') {
      await expect(row).toContainText('1/2'); await expect(row).toContainText('待核准');
    }
  }
  await page.getByRole('button', { name: '登出', exact: true }).click(); await login(page);
  await page.getByLabel('目前設定', { exact: true }).selectOption('asset');
  await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
  await page.locator('tbody tr').filter({ has: page.getByRole('button', { name: version, exact: true }) }).getByRole('button', { name: '套用版本' }).click();
  await page.getByLabel('確認版本切換影響').check();
  await expect(page.getByRole('button', { name: '確認並執行發布' })).toBeDisabled();
  const session = await (await page.request.get('/api/session')).json();
  const blocked = await page.request.post('/api/releases/' + release.id + '/publish', { data: { expected_active_id: null, confirmation: 'APPLY_VERSION', maintenance_window: false }, headers: { ...guard, 'X-CSRF-Token': session.csrf_token } });
  expect(blocked.status()).toBe(409); expect((await blocked.json()).error.code).toBe('MAINTENANCE_WINDOW_REQUIRED');
  await page.getByLabel('確認維護時段').check();
  const published = page.waitForResponse(r => r.url().endsWith('/publish') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '確認並執行發布' }).click(); const taskId = (await (await published).json()).id;
  await expect.poll(async () => (await (await page.request.get('/api/tasks/' + taskId)).json()).status, { timeout: 90000 }).toBe('completed');
  await expect(page.getByText('版本操作已完成，真實模型與設定驗證證據已保存。')).toBeVisible();
  measured.push({ stage: 'asset_double_approved_publish', task: await (await page.request.get('/api/tasks/' + taskId)).json() });
  await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
  await page.getByLabel('合成範例').selectOption('pump-medium-v1');
  const submitted = page.waitForResponse(r => r.url().endsWith('/api/inferences') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '執行並驗證' }).click(); const inferenceId = (await (await submitted).json()).id;
  await expect(page.getByRole('dialog').getByText('原文證據', { exact: true })).toBeVisible({ timeout: 70000 });
  const task = await (await page.request.get('/api/tasks/' + inferenceId)).json();
  expect(task.release_id).toBe(release.id); expect(task.profile_version).toBe(version);
  expect(task.generation.max_tokens).toBe(384); expect(task.generation.seed).toBe(84); expect(task.result.review_required).toBe(true);
  measured.push({ stage: 'asset_actual_inference', task, releases: await (await page.request.get('/api/releases')).json() });
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', { name: '停止引擎' }).click();
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true }).first()).toBeVisible();
});

test.afterEach(async ({ request }, testInfo) => {
  if (process.env.CONSOLE_REAL_MODEL === '1' && testInfo.status !== testInfo.expectedStatus) {
    const response = await request.post('/api/sessions', { data: { username: 'e2eadmin', password }, headers: guard });
    if (response.ok()) {
      const csrf = (await response.json()).csrf_token;
      await request.post('/api/engine/stop', { data: {}, headers: { ...guard, 'X-CSRF-Token': csrf } }).catch(() => {});
    }
  }
  if (measured.length) {
    await mkdir('../reports', { recursive: true });
    await writeFile('../reports/real-release-journey.json', JSON.stringify({ schema_version: '1.0', evidence_kind: 'measured', scope: 'local_real_Q8_service_configuration_release_not_weight_switch', stages: measured }, null, 2));
  }
});
