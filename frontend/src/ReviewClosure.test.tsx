import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EvidenceRetention, ReviewBackupWorkbench, ClosureQualityStatus } from './ReviewClosure';
afterEach(() => vi.restoreAllMocks());
describe('本機複核資料治理', () => {
  it('到期提示區分原始保存與人工內容，不宣稱永久保存', () => {
    render(<EvidenceRetention expires={1700000000} mode="local_review_opt_in" available={false} />);
    expect(screen.getByLabelText('複核證據保存與到期')).toHaveTextContent('明確同意本機7日');
    expect(screen.getByText(/接受與修正已由後端阻擋/)).toBeVisible();
    expect(screen.getByText(/人工摘要、理由與歷史可能含私人內容/)).toBeVisible();
  });
  it('一般使用者沒有備份操作入口', () => {
    render(<ReviewBackupWorkbench user={{ id: 'operator-demo', username: 'operator', role: 'operator' }} />);
    expect(screen.getByText(/匯出預覽與隔離還原需要管理者/)).toBeVisible();
    expect(screen.queryByRole('button', { name: '預覽複核備份範圍' })).not.toBeInTheDocument();
  });
  it('預覽可能含私人人工內容，需要明確確認才能匯出', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async input => new Response(JSON.stringify(String(input).endsWith('/export-preview') ? { preview_id: 'demo', bundle_sha256: 'a'.repeat(64), counts: { reviews: 1 }, includes: '人工摘要及理由', excludes: '原始證據', review_preview: [{ reason: '合成業務理由待確認' }] } : { receipts: [] }), { status: 200 }));
    render(<ReviewBackupWorkbench user={{ id: 'admin-demo', username: 'admin', role: 'admin' }} />);
    fireEvent.click(screen.getByRole('button', { name: '預覽複核備份範圍' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '確認匯出複核業務備份' })).toBeDisabled());
    expect(screen.getByLabelText('複核備份匯出預覽')).toHaveTextContent('合成業務理由待確認');
    fireEvent.click(screen.getByLabelText('確認人工私人內容備份'));
    expect(screen.getByRole('button', { name: '確認匯出複核業務備份' })).toBeEnabled();
  });
  it('品質候選與應用版本分開，尚未實跑時沒有虛構成績', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ application_version: '0.4.1', model_weight_sha256: 'a'.repeat(64), baseline: {}, candidates: [], known_defects: [], quality_limits: '已觀測，不是未見驗證', observed_evaluations: [] }), { status: 200 }));
    render(<ClosureQualityStatus />);
    await waitFor(() => expect(screen.getByText(/更新應用程式不等於發布模型候選/)).toBeVisible());
    expect(screen.getByText(/本輪三組已觀測評測尚未完成/)).toBeVisible();
  });
});
