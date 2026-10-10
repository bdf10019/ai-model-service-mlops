import { expect, test } from '@playwright/test';

test('desktop walkthrough: customization, release, review, evidence and downloads', async ({ page }) => {
  const apiCalls: string[] = [];
  page.on('request', request => {
    if (/127\.0\.0\.1:8120|\/api\//.test(request.url())) apiCalls.push(request.url());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('每一次交付');

  await page.getByRole('group', { name: '實驗台團隊切換' }).getByRole('button', { name: '設備管理團隊' }).click();
  await page.getByRole('button', { name: '驗證並預覽' }).click();
  await expect(page.getByText(/欄位錯誤已阻擋預覽/)).toBeVisible();
  await page.getByLabel('設備重要性').selectOption({ label: '關鍵' });
  await page.getByLabel('維護狀態').selectOption({ label: '維護時段可用' });
  await page.getByRole('button', { name: '驗證並預覽' }).click();
  await expect(page.getByText(/欄位錯誤已阻擋預覽/)).toBeVisible();
  await page.getByLabel('軸承溫度來源').selectOption({ label: '設備感測器' });
  await page.getByRole('button', { name: '驗證並預覽' }).click();
  await expect(page.getByText(/水泵資產守望台預覽完成/)).toBeVisible();

  const replayNext = page.getByRole('button', { name: '重播下一步' });
  await replayNext.click(); await replayNext.click(); await replayNext.click();
  await expect(page.getByText('狀態未變')).toBeVisible();
  await expect(replayNext).toBeDisabled();

  await page.getByRole('group', { name: '複核團隊切換' }).getByRole('button', { name: '設備管理團隊' }).click();
  await page.getByRole('button', { name: '接受建議' }).click();
  await expect(page.getByRole('heading', { name: '第一位複核者已確認' })).toBeVisible();
  await page.getByRole('button', { name: '第二位複核者確認' }).click();
  await expect(page.getByText(/兩位不同複核者已完成/).first()).toBeVisible();

  await page.getByRole('group', { name: '歷史負載併發' }).getByRole('button', { name: '併發 2' }).click();
  await expect(page.getByText('886.79 ms').first()).toBeVisible();
  await page.getByRole('tablist', { name: '品質資料版本' }).getByRole('tab', { name: 'holdout-v2' }).click();
  await page.getByRole('group', { name: '結果層次' }).getByRole('button', { name: '政策後' }).click();
  await expect(page.getByText('17/22 · 不可降低安全政策後結果')).toBeVisible();
  await page.getByRole('button', { name: /h15/ }).click();
  await expect(page.getByText(/退步保留/)).toBeVisible();

  await expect(page.getByRole('link', { name: /Markdown作品摘要/ })).toHaveAttribute('href', '/downloads/作品摘要.md');
  await expect(page.getByRole('link', { name: /JSON證據摘要/ })).toHaveAttribute('href', '/downloads/evidence-summary.json');
  expect(apiCalls).toEqual([]);
  await page.screenshot({ path: 'reports/showcase-desktop.png', fullPage: true });
});

test('mobile navigation and image explorer remain operable without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?profile=asset&scenario=critical');
  await page.getByRole('button', { name: /九章導覽/ }).click();
  const nav = page.getByRole('navigation', { name: '九章導覽' });
  await expect(nav).toBeVisible();
  await nav.getByRole('link', { name: '08 成果與限制', exact: true }).click();
  await expect(page.getByRole('button', { name: /九章導覽/ })).toHaveAttribute('aria-expanded', 'false');
  const sizes = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(sizes.scroll).toBeLessThanOrEqual(sizes.client + 1);

  const screenshotButton = page.getByRole('button', { name: /服務總覽控制台畫面/ });
  await screenshotButton.click();
  await expect(page.getByRole('dialog', { name: '服務總覽' })).toBeVisible();
  await expect(page.getByRole('button', { name: '關閉產品畫面' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '服務總覽' })).toBeHidden();

  await page.getByRole('group', { name: '結果層次' }).getByRole('button', { name: '政策後' }).click();
  await page.screenshot({ path: 'reports/showcase-mobile.png', fullPage: true });
});

test('secondary controls change explanatory state and bundled assets resolve', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.getByRole('textbox', { name: '設備編號' })).toHaveValue('EQ-PUMP-001');
  await page.getByRole('group', { name: '合成情境' }).getByRole('button').nth(1).click();
  await expect(page.getByText('循環水泵間歇震動，尚未提供負載、振動量測值與軸承溫度來源。').first()).toBeVisible();
  await page.getByRole('button', { name: '分享展示狀態' }).click();
  await expect(page.getByRole('status').first()).toContainText('沒有上傳或修改控制台');
  await expect(page).toHaveURL(/profile=service&scenario=missing/);

  await page.getByRole('tab', { name: /評測/ }).click();
  await expect(page.getByText('固定報告＋分母')).toBeVisible();
  await page.getByRole('button', { name: '重播下一步' }).click();
  await page.getByRole('button', { name: '重新開始' }).click();
  await expect(page.getByText('已建立歷史候選')).toBeVisible();

  await page.getByRole('button', { name: '資訊不足', exact: true }).click();
  await expect(page.getByText(/已標記資訊不足/).first()).toBeVisible();
  await page.getByLabel('維運指標').selectOption('generation');
  await page.getByRole('group', { name: '歷史負載併發' }).getByRole('button', { name: '併發 2' }).click();
  await expect(page.getByText('1140.25 ms').first()).toBeVisible();

  for (const path of ['/downloads/作品摘要.md', '/downloads/evidence-summary.json', '/images/console-overview.png']) {
    const response = await request.get(path);
    expect(response.ok(), `${path} should be bundled`).toBeTruthy();
  }
  const regression = await request.get('/data/pump-demo-regression-v1.json');
  expect(regression.ok()).toBeTruthy();
});

test('every interactive control is named and the printable action executes locally', async ({ page }) => {
  await page.addInitScript(() => { window.print = () => document.documentElement.dataset.printRequested = 'true'; });
  await page.goto('/');
  const controls = page.locator('button:visible, a[href]:visible, select:visible, textarea:visible, input:visible');
  const count = await controls.count();
  expect(count).toBeGreaterThan(60);
  for (let index = 0; index < count; index += 1) {
    await expect(controls.nth(index), `control ${index} must have an accessible name`).toHaveAccessibleName(/.+/);
  }
  await page.getByRole('button', { name: /列印HTML作品摘要/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-print-requested', 'true');
});
