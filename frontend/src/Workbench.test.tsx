import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConfigEditor } from './Workbench';
import type { Profile } from './api';

const profile: Profile = { id: 'service', version: 'service-v1', name: '維修分流台', team: '維修服務團隊', accent: '#087f8c', noun: '案件', focus: '接案摘要與缺失資訊', prompt_version: 'triage-service-v1', required_fields: ['incident_text', 'asset_id'], review_severities: ['high', 'critical', 'unknown'], review_critical_assets: false, approvals_required: 1, maintenance_window_required: false, queue_limit: 4, queue_timeout_seconds: 10, generation_timeout_seconds: 60, max_tokens: 256, temperature: 0, seed: 42, quality_gate: null };
const admin = { id: 'synthetic-admin', username: 'testadmin', role: 'admin' as const };
afterEach(() => vi.restoreAllMocks());

describe('版本化設定表單', () => {
  it('未保存的參數不可套用，保存不悄悄更新線上版本', async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); calls.push(url + ':' + (init?.method || 'GET'));
      if (url === '/api/configs' && init?.method === 'POST') {
        const config = { ...JSON.parse(String(init.body)), version: 'service-r-test' };
        return new Response(JSON.stringify({ id: config.version, config, sha256: 'synthetic' }));
      }
      return new Response(JSON.stringify({ versions: [{ id: profile.version, config: profile, created_at: 1 }], defaults: { service: profile }, selected: { service: profile.version } }));
    });
    render(<ConfigEditor profile={profile} user={admin} changed={vi.fn()} />);
    await screen.findByRole('option', { name: 'service-v1 · 沙盒候選' });
    fireEvent.change(screen.getByLabelText('Token 上限'), { target: { value: '320' } });
    expect(screen.getByRole('button', { name: '套用到沙盒候選' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存新版本' }));
    await screen.findByText('新設定版本已保存；線上版本及既有任務保持原快照。');
    expect(calls.some(c => c.includes('/select') || c.includes('/publish'))).toBe(false);
  });

  it('後端拒絕安全規則時顯示錯誤且保留草稿供修正', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).endsWith('/preview')) return new Response(JSON.stringify({ error: { code: 'SAFETY_FLOOR_REQUIRED', request_id: 'synthetic-request' } }), { status: 422 });
      return new Response(JSON.stringify({ versions: [{ id: profile.version, config: profile, created_at: 1 }], defaults: {}, selected: {} }));
    });
    render(<ConfigEditor profile={profile} user={admin} changed={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('產品名稱'), { target: { value: '原創維修新名稱' } });
    fireEvent.click(screen.getByRole('button', { name: '驗證與預覽' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('設定缺少不可降低的安全底線'));
    expect(screen.getByLabelText('產品名稱')).toHaveValue('原創維修新名稱');
  });

  it('一般使用者可保存草稿但不能套用共用候選', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ versions: [{ id: profile.version, config: profile }], defaults: {}, selected: {} })));
    render(<ConfigEditor profile={profile} user={{ ...admin, role: 'operator' }} changed={vi.fn()} />);
    expect(screen.getByRole('button', { name: '套用到沙盒候選' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存新版本' })).toBeEnabled();
  });
});
