import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
const password = process.env.E2E_PASSWORD!;
const guard = { 'X-Console-Request': '1', Origin: 'http://127.0.0.1:18120' };
async function loginAs(page: Page, name: string) {
  await page.context().clearCookies(); await page.goto('/');
  await page.getByLabel('帳號', { exact: true }).fill(name); await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登入控制台' }).click();
  await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible();
}
async function createReview(page: Page, profile = 'service') {
  const session = await page.request.get('/api/session');
  const headers = { ...guard, 'X-CSRF-Token': (await session.json()).csrf_token, 'Idempotency-Key': crypto.randomUUID() };
  const r = await page.request.post('/api/inferences', { data: { profile_id: profile, provider_id: 'fixture', mode: 'sandbox', incident_text: '配電箱出現火花，現場尚未恢復正常。', asset_id: 'DEMO-REVIEW', asset_criticality: 'standard', maintenance_window: 'available', review_retention: 'local_review_7_days' }, headers });
  expect(r.status()).toBe(202); const id = (await r.json()).id;
  await expect.poll(async () => (await (await page.request.get('/api/tasks/' + id)).json()).status).toBe('needs_review');
  return { id, headers };
}
async function openReview(page: Page, id: string, profile = 'service') {
  await page.getByLabel('目前設定', { exact: true }).selectOption(profile);
  await page.getByRole('button', { name: '人工複核收件匣', exact: true }).click();
  await page.getByRole('button', { name: '開啟複核 ' + id.slice(0, 8), exact: true }).click();
}
test.beforeAll(async ({ request }) => { const r = await request.post('/api/bootstrap', { data: { username: 'e2eadmin', password }, headers: guard }); expect([201, 409]).toContain(r.status()); });
test.beforeEach(async ({ page }) => {
  await page.goto('/'); await page.getByLabel('帳號', { exact: true }).fill('e2eadmin'); await page.getByLabel('密碼', { exact: true }).fill(password); await page.getByRole('button', { name: '登入控制台' }).click();
  await expect(page.getByRole('heading', { name: '服務總覽', exact: true })).toBeVisible();
});
test('逐筆否定錯誤、原始政策差異與來源鑽取', async ({ page }) => {
  await page.getByRole('button', { name: '判讀錯誤與證據', exact: true }).click();
  await page.getByLabel('錯誤分析類型').selectOption('policy_over_escalation');
  await page.getByRole('button', { name: 'u04 判讀證據' }).click();
  await expect(page.getByLabel('逐筆錯誤來源')).toContainText('沒有超限紀錄');
  await expect(page.getByLabel('逐筆錯誤來源')).toContainText('severity');
  await page.getByRole('button', { name: '鑽取原始事件判斷正確率' }).click();
  await expect(page.getByLabel('品質指標來源')).toContainText('分母案例 14');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
});
test('推論自願保存、人工修正與原始證據不覆寫', async ({ page }) => {
  await page.getByLabel('目前設定', { exact: true }).selectOption('service');
  await page.getByRole('button', { name: '推論 Playground', exact: true }).click();
  await page.getByLabel('Provider', { exact: true }).selectOption('fixture');
  await page.getByLabel('本機複核證據保存同意').check();
  await page.getByLabel('合成範例').selectOption('pump-medium-v1');
  await page.getByLabel('事件文字', { exact: true }).fill('配電箱出現火花，設備尚未恢復正常。');
  const started = page.waitForResponse(r => r.url().endsWith('/api/inferences') && r.request().method() === 'POST');
  await page.getByRole('button', { name: '執行並驗證' }).click();
  const id = (await (await started).json()).id;
  await expect(page.getByRole('dialog').getByText('原文證據', { exact: true })).toBeVisible();
  const original = await (await page.request.get('/api/tasks/' + id)).json();
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  await page.getByRole('button', { name: '人工複核收件匣', exact: true }).click();
  await page.getByRole('button', { name: '開啟複核 ' + id.slice(0, 8), exact: true }).click();
  await page.getByLabel('業務處理方式').selectOption('correct');
  await page.getByLabel('修正業務摘要').check();
  await page.getByLabel('人工業務摘要').fill('已核對真實火花，等待現場主管處理。');
  await page.getByLabel('業務處理理由').fill('核對本次輸入證據後補充業務摘要，不執行設備動作。');
  await page.getByRole('button', { name: '保存業務複核處理' }).click();
  await expect(page.getByLabel('另存人工結果')).toContainText('等待現場主管處理');
  const after = await (await page.request.get('/api/tasks/' + id)).json();
  expect(after.raw_output).toEqual(original.raw_output); expect(after.result).toEqual(original.result);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '匯出最終業務結果' }).click();
  expect((await download).suggestedFilename()).toContain('human-result');
  await page.screenshot({ path: '../reports/review-inbox.png', fullPage: true });
});

test('設備版後端拒絕管理者代審，兩名複核者完成高風險流程', async ({ page }) => {
  const { id, headers } = await createReview(page, 'asset');
  for (const username of ['businessone', 'businesstwo']) {
    const response = await page.request.post('/api/users', { data: { username, password, role: 'reviewer' }, headers });
    expect([201, 409]).toContain(response.status());
  }
  await openReview(page, id, 'asset');
  await expect(page.getByText('目前登入角色不能處理，或來源已不可取得；切換團隊不會改變登入權限。')).toBeVisible();
  await loginAs(page, 'businessone'); await openReview(page, id, 'asset');
  await page.getByLabel('複核證據 1', { exact: true }).check();
  await page.getByLabel('業務處理理由').fill('已核對現場危害與輸入證據，提交第一名業務複核建議。');
  await page.getByRole('button', { name: '保存業務複核處理' }).click();
  await expect(page.getByLabel('另存人工結果')).toContainText('待獨立確認');
  expect((await (await page.request.get('/api/reviews/' + id)).json()).state).toBe('awaiting_second');
  await loginAs(page, 'businesstwo'); await openReview(page, id, 'asset');
  await page.getByLabel('複核證據 1', { exact: true }).check();
  await page.getByLabel('業務處理理由').fill('獨立核對本次輸入及第一名處理歷史，確認業務分流結果。');
  await page.getByRole('button', { name: '保存業務複核處理' }).click();
  await expect(page.getByRole('button', { name: '匯出最終業務結果' })).toBeVisible();
  const final = await (await page.request.get('/api/reviews/' + id)).json();
  expect(final.state).toBe('accepted'); expect(new Set(final.approvals).size).toBe(2);
});

test('過期草稿收到版本衝突，明確載入最新紀錄恢復', async ({ page }) => {
  const { id, headers } = await createReview(page);
  await openReview(page, id);
  await page.getByLabel('業務處理理由').fill('此頁草稿已核對證據，但尚未讀取其他人的最新結果。');
  const response = await page.request.post('/api/reviews/' + id + '/decisions', { data: { expected_revision: 0, decision: 'return', reason: '另一處理已決定退回來源，保留原始證據與歷史。', changes: {}, evidence_indices: [] }, headers });
  expect(response.status()).toBe(200);
  await page.getByRole('button', { name: '保存業務複核處理' }).click();
  await expect(page.getByRole('button', { name: '重新載入最新複核紀錄' })).toBeVisible();
  await page.getByRole('button', { name: '重新載入最新複核紀錄' }).click();
  await expect(page.getByLabel('業務複核詳情')).toContainText('已退回');
  await expect(page.getByRole('button', { name: '保存業務複核處理' })).toHaveCount(0);
});
