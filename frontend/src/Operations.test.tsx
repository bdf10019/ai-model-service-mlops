import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { BackupWorkbench, MonitoringWorkbench, AlertPolicyEditor } from './Operations';
import { api } from './api';
import type { Profile } from './api';
vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), api: vi.fn() }));
const user = { id: 'test', username: 'testadmin', role: 'admin' as const };
beforeEach(() => vi.mocked(api).mockReset());
afterEach(cleanup);

test('monitor empty data and real chart drill links request without fabricating metrics', async () => {
  const open = vi.fn();
  vi.mocked(api).mockImplementation(async path => {
    if (path === '/configs') return { versions: [{ id: 'service-v1' }] };
    if (path === '/alerts') return { alerts: [] };
    return { summary: { success_rate: null, generation_ms_p95: null }, resources: [], sample_errors: [], definitions: {}, limits: {}, http: [], buckets: [{ from: 1, to: 2, request_count: 1, task_ids: ['request1'] }], tasks: [{ id: 'request1', trace_id: 'trace1', profile_version: 'service-v1', status: 'completed', error: null }] };
  });
  render(<MonitoringWorkbench user={user} openTask={open} />);
  await screen.findByRole('button', { name: '鑽取時段 1' });
  expect(screen.getAllByText('未提供').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button', { name: '鑽取時段 1' }));
  fireEvent.click(screen.getByRole('button', { name: 'request1' }));
  expect(open).toHaveBeenCalledWith('request1');
  expect(screen.getByText(/尚無資源樣本/)).toBeTruthy();
});

test('operator backup has visible permission and recovery explanation', () => {
  render(<BackupWorkbench user={{ ...user, role: 'operator' }} />);
  expect(screen.getByText(/需要管理者/)).toBeTruthy();
  expect(vi.mocked(api)).not.toHaveBeenCalled();
});

test('alert policy preview labels fixture and leaves apply disabled until version saved', async () => {
  const policy = { profile_id: 'service', latency_ms: 2000, error_rate: .2, queue_ms: 2000, window_seconds: 60, minimum_samples: 3, sustain_seconds: 10, recovery_seconds: 10, recovery_ratio: .8, minimum_free_ram_mib: 768 };
  vi.mocked(api).mockImplementation(async path => path === '/alert-policies' ? { versions: [{ id: 'p1', policy, sha256: 'test' }], selected: { service: 'p1' } } : { evidence_kind: 'fixture_policy_preview_only', persisted: false });
  render(<AlertPolicyEditor profile={{ id: 'service' } as Profile} user={user} />);
  await screen.findByLabelText('告警最低樣本量');
  expect((screen.getByRole('button', { name: '確認套用監控政策' }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: '驗證與預覽監控政策' }));
  await waitFor(() => expect(screen.getByText('受控預覽 · Fixture，不是實測')).toBeTruthy());
});
