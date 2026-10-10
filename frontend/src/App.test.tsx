import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { Login, Playground, TaskDetail } from './App';
import type { Profile, Task } from './api';
const profile: Profile = { id: 'asset', version: 'asset-v1', name: '設備守望台', team: '設備管理團隊', accent: '#5654b8', noun: '設備事件', focus: '風險', prompt_version: 'triage-asset-v1', required_fields: ['incident_text', 'asset_id', 'asset_criticality', 'maintenance_window'], review_severities: ['medium', 'high', 'critical', 'unknown'], review_critical_assets: true, approvals_required: 2, maintenance_window_required: true, queue_limit: 2, queue_timeout_seconds: 5, generation_timeout_seconds: 60, max_tokens: 256, temperature: 0, seed: 42, quality_gate: null };
afterEach(() => vi.restoreAllMocks());
describe('產品表單與證據', () => {
  it('回滾後的自動模式使用發布欄位，明示沙盒才使用新候選欄位', () => {
    const candidate: Profile = { ...profile, id: 'service', version: 'service-r-new', review_critical_assets: false };
    const released: Profile = { ...candidate, version: 'service-r-old', required_fields: ['incident_text', 'asset_id'], review_severities: ['high', 'critical', 'unknown'] };
    render(<Playground profile={candidate} activeProfile={released} samples={[]} ready onTask={vi.fn()} />);
    expect(screen.getByLabelText('設備重要性')).not.toBeRequired();
    expect(screen.getByText(/本次執行設定：service-r-old/)).toBeVisible();
    fireEvent.change(screen.getByLabelText('執行版本'), { target: { value: 'sandbox' } });
    expect(screen.getByLabelText('設備重要性')).toBeRequired();
    expect(screen.getByText(/本次執行設定：service-r-new/)).toBeVisible();
  });
  it('初始化使用真實密碼欄位與基本限制', () => {
    render(<Login initialized={false} onLogin={vi.fn()} />);
    expect(screen.getByLabelText('密碼')).toHaveAttribute('type', 'password');
    expect(screen.getByLabelText('密碼')).toHaveAttribute('minlength', '12');
    expect(screen.getByLabelText('帳號')).toHaveAttribute('required');
  });
  it('真實引擎不可用時禁止提交，Fixture 必須由使用者選擇', () => {
    render(<Playground profile={profile} samples={[]} ready={false} onTask={vi.fn()} />);
    expect(screen.getByRole('button', { name: /執行並驗證/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Provider'), { target: { value: 'fixture' } });
    expect(screen.getByRole('button', { name: /執行並驗證/ })).toBeEnabled();
    expect(screen.getByText(/Fixture 測試：輸出與速度不代表真實模型/)).toBeVisible();
  });
  it('設備設定的額外欄位為必填', () => {
    render(<Playground profile={profile} samples={[]} ready={true} onTask={vi.fn()} />);
    expect(screen.getByLabelText('設備重要性')).toBeRequired();
    expect(screen.getByLabelText('維護時段')).toBeRequired();
  });
  it('失敗任務不冒充正式結果或 Token-level TTFT', () => {
    const t: Task = { id: 'synthetic-task', trace_id: 'synthetic-trace', owner: 'synthetic', kind: 'inference', status: 'failed', created_at: 1, profile_version: 'asset-v1', evidence_kind: 'fixture', error: 'MODEL_OUTPUT_INVALID', events: [{ stage: 'failed', time: 1 }] };
    render(<TaskDetail task={t} />);
    expect(screen.getAllByText(/模型輸出未通過 Schema/).length).toBeGreaterThan(0);
    expect(screen.getByText(/未量測 · 本切片未請求串流/)).toBeVisible();
    expect(screen.queryByText('原文證據')).not.toBeInTheDocument();
  });
});
