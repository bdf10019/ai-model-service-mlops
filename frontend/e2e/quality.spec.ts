import { test, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
const password = process.env.E2E_PASSWORD!;
const guard = { 'X-Console-Request': '1', Origin: 'http://127.0.0.1:18120' };
test('品質候選固定評測與人工負擔鑽取', async ({ page }) => {
  test.skip(process.env.CONSOLE_REAL_MODEL !== '1', '僅在明確開啟既有本機 Q8 驗證時執行。');
  test.setTimeout(360000);
  const previouslyObserved = existsSync('../reports/holdout-v2-observations.json');
  const policyFix = process.env.CONSOLE_POLICY_FIX === '1';
  const init = await page.request.post('/api/bootstrap', { data: { username: 'e2eadmin', password }, headers: guard });
  expect([201, 409]).toContain(init.status());
  await page.context().clearCookies();
  await page.goto('/'); await page.getByLabel('帳號', { exact: true }).fill('e2eadmin'); await page.getByLabel('密碼', { exact: true }).fill(password);
  await page.getByRole('button', { name: '登入控制台' }).click();
  await page.getByRole('button', { name: '模型與 Provider', exact: true }).click();
  await page.getByRole('button', { name: '啟動候選引擎' }).click();
  await expect(page.getByRole('dialog').getByText('已完成', { exact: true }).first()).toBeVisible({ timeout: 90000 });
  await page.getByRole('button', { name: '關閉任務詳情' }).click();
  const records: unknown[] = [];
  let releaseCheck: unknown = null;
  try {
    await page.getByRole('button', { name: '客製化設定', exact: true }).click();
    await page.getByRole('button', { name: policyFix ? '載入政策修補候選' : '載入本輪品質候選設定' }).click();
    await page.getByRole('button', { name: '驗證與預覽', exact: true }).click();
    await expect(page.getByRole('heading', { name: '同一合成案例預覽 · Fixture' })).toBeVisible();
    const saving = page.waitForResponse(r => r.url().endsWith('/api/configs') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '保存新版本', exact: true }).click();
    const version = (await (await saving).json()).id;
    await page.getByRole('button', { name: '套用到沙盒候選', exact: true }).click();
    const sets = ['equipment-triage-v1', 'equipment-holdout-v1', ...(process.env.CONSOLE_NEW_HOLDOUT === '1' || policyFix ? ['equipment-holdout-v2'] : [])];
    for (const set of sets) {
      await page.getByRole('button', { name: '評測與版本比較', exact: true }).click();
      await page.getByLabel('評測設定版本').selectOption(version);
      await page.getByLabel('評測模型').selectOption('q8');
      await page.getByLabel('固定評測集').selectOption(set);
      const starting = page.waitForResponse(r => r.url().endsWith('/api/evaluations') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '執行正式評測', exact: true }).click();
      const id = (await (await starting).json()).id;
      await expect.poll(async () => (await (await page.request.get('/api/evaluations/' + id)).json()).status, { timeout: 100000 }).toBe('completed');
      const report = await (await page.request.get('/api/evaluations/' + id)).json();
      expect(report.completed_cases).toBe(set === 'equipment-holdout-v1' ? 16 : 24);
      expect(report.quality_metrics.human_decisions_included).toBe(false);
      if (set === 'equipment-holdout-v2') expect(report.evidence_use).toBe(previouslyObserved ? 'holdout_v2_observed_or_changed_candidate_development' : 'holdout_v2_first_frozen_candidate');
      await page.getByRole('button', { name: '鑽取政策後自動完成中的正確率', exact: true }).click();
      await expect(page.getByLabel('品質指標來源')).toBeVisible();
      records.push(report);
    }
    if (process.env.CONSOLE_NEW_HOLDOUT === '1' || policyFix) {
      await page.getByRole('button', { name: '發布與回滾', exact: true }).click();
      await page.getByLabel('待檢查評測').selectOption((records[0] as { id: string }).id);
      const checking = page.waitForResponse(r => r.url().endsWith('/releases/check'));
      await page.getByRole('button', { name: '檢查發布資格' }).click();
      releaseCheck = await (await checking).json();
      if (!(releaseCheck as { passed: boolean }).passed) await expect(page.getByRole('button', { name: '建立發布提案' })).toBeDisabled();
    }
    await page.screenshot({ path: '../reports/quality-workbench.png', fullPage: true });
  } finally {
    await mkdir('../reports', { recursive: true });
    const output = policyFix ? 'quality-fix-0.4.1.json' : process.env.CONSOLE_NEW_HOLDOUT === '1' ? (previouslyObserved ? 'quality-observed-validation-' + Date.now() + '.json' : 'quality-first-validation.json') : 'quality-development.json';
    await writeFile('../reports/' + output, JSON.stringify({ evidence_kind: 'measured', author: 'AI-authored synthetic labels; no independent human review', reports: records, release_check: releaseCheck }, null, 2));
    const session = await page.request.post('/api/sessions', { data: { username: 'e2eadmin', password }, headers: guard });
    if (session.ok()) await page.request.post('/api/engine/stop', { data: {}, headers: { ...guard, 'X-CSRF-Token': (await session.json()).csrf_token } });
  }
});
