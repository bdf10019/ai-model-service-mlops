import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { QualityMetricsPanel } from './ReviewWorkbench';
describe('品質與人工負擔來源', () => {
  it('顯示分母與逐筆來源，不把人工結果加進模型成績', () => {
    const record = { definition: '固定應複核案例分母', numerator: 1, denominator: 2, value: .5, case_ids: ['A', 'B'], numerator_case_ids: ['A'] };
    render(<QualityMetricsPanel metrics={{ version: 'triage-quality-v2', human_decisions_included: false, records: { post_required_review_recall: record } }} cases={[{ id: 'A', input: { incident_text: '真實風險反例' }, expected: {}, raw_output: null, result: null }, { id: 'B', input: {}, expected: {}, raw_output: null, result: null }]} />);
    fireEvent.click(screen.getByRole('button', { name: '鑽取政策後應轉人工召回率' }));
    expect(screen.getByLabelText('品質指標來源')).toHaveTextContent('分母案例 2；符合分子 1');
    expect(screen.getByText('A · 列入分子')).toBeVisible();
    expect(screen.getByText('B · 僅列入分母')).toBeVisible();
    expect(screen.getByText(/人工接受／修正不列入/)).toBeVisible();
  });
  it('零自動完成案例明確標示無樣本，不能冒充100%', () => {
    render(<QualityMetricsPanel metrics={{ version: 'triage-quality-v2', human_decisions_included: false, records: { post_automatic_accuracy: { definition: '自動案例', numerator: 0, denominator: 0, value: null, case_ids: [], numerator_case_ids: [] } } }} cases={[]} />);
    expect(screen.getByRole('button', { name: '鑽取政策後自動完成中的正確率' })).toHaveTextContent('未提供（無樣本） · 0/0');
  });
});
