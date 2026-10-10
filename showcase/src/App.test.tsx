import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import App from './App';

describe('offline showcase journeys', () => {
  beforeEach(() => history.replaceState(null, '', '/'));
  afterEach(cleanup);

  test('explains the product and keeps the nine chapters available', () => {
    render(<App />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('讓 AI 的每一次交付');
    expect(screen.getAllByText(/循環水泵/).length).toBeGreaterThan(0);
    expect(screen.getByDisplayValue('EQ-PUMP-001')).toBeInTheDocument();
    expect(within(screen.getByRole('navigation', { name: '九章導覽' })).getAllByRole('link')).toHaveLength(9);
    expect(screen.getByText('工程驗證不等於模型合格。')).toBeInTheDocument();
  });

  test('asset customization blocks incomplete fields then previews real profile differences', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(within(screen.getByRole('group', { name: '實驗台團隊切換' })).getByRole('button', { name: '設備管理團隊' }));
    await user.click(screen.getByRole('button', { name: '驗證並預覽' }));
    expect(screen.getByText(/欄位錯誤已阻擋預覽/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('設備重要性'), '關鍵');
    await user.selectOptions(screen.getByLabelText('維護狀態'), '維護時段可用');
    await user.click(screen.getByRole('button', { name: '驗證並預覽' }));
    expect(screen.getByText(/欄位錯誤已阻擋預覽/)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('軸承溫度來源'), '設備感測器');
    await user.click(screen.getByRole('button', { name: '驗證並預覽' }));
    expect(screen.getByText(/水泵資產守望台預覽完成/)).toBeInTheDocument();
    expect(screen.getByText(/兩名不同 reviewer/)).toBeInTheDocument();
  });

  test('canonical pump case preserves negative safety evidence', () => {
    render(<App />);
    expect(screen.getAllByText(/現場未發現洩漏、火花或焦味/).length).toBeGreaterThan(0);
    expect(screen.getByText(/不能被誤讀成已發生危害/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('無洩漏、火花或焦味')).toBeInTheDocument();
  });

  test('release replay ends blocked and never claims a release', async () => {
    const user = userEvent.setup();
    render(<App />);
    const next = screen.getByRole('button', { name: '重播下一步' });
    await user.click(next);
    expect(screen.getByText('拒絕')).toBeInTheDocument();
    await user.click(next);
    await user.click(next);
    expect(screen.getByText('狀態未變')).toBeInTheDocument();
    expect(next).toBeDisabled();
    expect(screen.getAllByText(/v5／v5.1未發布/).length).toBeGreaterThan(0);
  });

  test('manual result is stored separately and asset flow requires stronger evidence', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(within(screen.getByRole('group', { name: '複核團隊切換' })).getByRole('button', { name: '設備管理團隊' }));
    await user.click(screen.getByRole('button', { name: '修正允許欄位' }));
    await user.clear(screen.getByLabelText('修正理由'));
    await user.type(screen.getByLabelText('修正理由'), '過短');
    await user.click(screen.getByRole('button', { name: '另存人工結果' }));
    expect(screen.getByRole('heading', { name: '需要至少16字理由與證據參照' })).toBeInTheDocument();
    await user.type(screen.getByLabelText('修正理由'), '，現場量測資料與維護紀錄均已逐項核對完成');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: '另存人工結果' }));
    expect(screen.getAllByText(/原始與政策結果沒有覆寫/).length).toBe(2);
  });

  test('evidence layers expose denominators, regressions and offline downloads', async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(within(screen.getByRole('tablist', { name: '品質資料版本' })).getByRole('tab', { name: 'holdout-v2' }));
    await user.click(screen.getByRole('button', { name: '政策後' }));
    expect(screen.getByText('不可降低安全政策後結果').closest('p')).toHaveTextContent('17/22 · 不可降低安全政策後結果');
    await user.click(screen.getByRole('button', { name: /h15/ }));
    expect(screen.getByText(/退步保留/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Markdown作品摘要/ })).toHaveAttribute('download');
    expect(screen.getByRole('link', { name: /JSON證據摘要/ })).toHaveAttribute('download');
    expect(screen.getByRole('link', { name: /本機已啟動：開啟控制台/ })).toHaveAttribute('href', 'http://127.0.0.1:8120/');
    expect(screen.getByRole('link', { name: /尚未啟動：查看啟動方式/ })).toHaveAttribute('href', '#launch-guide');
  });
});
