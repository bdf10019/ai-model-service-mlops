import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, setCsrf, ApiError, errorMessage, terminal, date, statusText, roleText, eventText, severityText, actionText } from './api';
import type { User, Profile, Sample, Health, Model, Task, Audit } from './api';
import { MonitoringWorkbench, LoadWorkbench, BackupWorkbench, AlertPolicyEditor } from './Operations';
import { ConfigEditor, EvaluationWorkbench, ReleaseWorkbench } from './Workbench';
import { ClosureQualityStatus, ReviewBackupWorkbench } from './ReviewClosure';
import { ReviewInbox, ReviewPolicyEditor, ErrorAnalysisExplorer } from './ReviewWorkbench';

type Page = 'overview' | 'models' | 'profiles' | 'playground' | 'tasks' | 'trace' | 'accounts' | 'evaluations' | 'releases' | 'monitoring' | 'loads' | 'backups' | 'quality' | 'reviews';
function Badge({ text, kind = '' }: { text: string; kind?: string }) { return <span className={'badge ' + kind}>{text}</span>; }
function Empty({ children }: { children: React.ReactNode }) { return <div className="empty"><span aria-hidden="true">◇</span><p>{children}</p></div>; }
function Notice({ error, onDismiss }: { error: Error | null; onDismiss?: () => void }) {
  if (!error) return null;
  return <div className="notice error" role="alert"><div><strong>{error.message}</strong>{error instanceof ApiError && error.requestId && <small>追蹤編號：{error.requestId}</small>}</div>{onDismiss && <button className="text-button" onClick={onDismiss}>關閉</button>}</div>;
}

export function Login({ initialized, onLogin }: { initialized: boolean; onLogin: (user: User, csrf: string) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const data = await api<{ user: User; csrf_token: string }>(initialized ? '/sessions' : '/bootstrap', 'POST', { username, password });
      setPassword(''); onLogin(data.user, data.csrf_token);
    } catch (e) { setError(e as Error); } finally { setBusy(false); }
  }
  return <div className="login-shell">
    <section className="login-story"><div className="logo"><span>m</span> MODEL SERVICE</div><div className="login-story-main"><p className="eyebrow">AI 模型服務與 MLOps</p><h1>從一個模型，<br />到一套可治理的服務。</h1><p className="lead">選擇設定、真實推論、檢視證據。<br />每個決策都保留版本，每個結果都有來源。</p><div className="login-flow"><span>設定</span><i>→</i><span>推論</span><i>→</i><span>驗證</span><i>→</i><span>追溯</span></div></div><p className="login-foot">本機控制台 · 候選沙盒 · 原創合成案例</p></section>
    <main className="login-form"><Badge text="設定到發布版本" kind="teal" /><h2>{initialized ? '歡迎回到控制台' : '建立第一位管理者'}</h2><p className="muted">{initialized ? '使用你的本機帳號登入。角色與操作權限由後端驗證。' : '帳號僅用於此本機安裝。完成後可建立一般使用者與複核者。'}</p><Notice error={error} /><form onSubmit={submit}><label>帳號<input autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} required pattern="[a-z][a-z0-9_-]{2,31}" minLength={3} maxLength={32} placeholder="小寫英文字母開頭" /></label><small className="field-hint">3–32 字元，可使用小寫英文字母、數字、底線或短橫線。</small><label>密碼<input type="password" autoComplete={initialized ? 'current-password' : 'new-password'} value={password} onChange={e => setPassword(e.target.value)} required minLength={12} maxLength={128} placeholder="至少 12 字元" /></label><button className="primary full" disabled={busy}>{busy ? '驗證中…' : initialized ? '登入控制台' : '建立並進入控制台'}<span aria-hidden="true">↗</span></button></form><div className="login-note"><strong>目前可操作</strong><p>健康檢查、模型驗證與啟動、設定比較、Playground、佇列與事件追溯。</p><small>固定評測、版本比較、獨立核准、發布與回滾已提供；容器實跑依環境驗證。</small></div></main>
  </div>;
}

export default function App() {
  const [setup, setSetup] = useState<boolean | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const currentUser = useRef('');
  const refreshEpoch = useRef(0);
  const [page, setPage] = useState<Page>('overview');
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [activeProfiles, setActiveProfiles] = useState<Record<string, Profile>>({});
  const [profileId, setProfileId] = useState(sessionStorage.getItem('profile') || 'service');
  const [samples, setSamples] = useState<Sample[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [history, setHistory] = useState<Health[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<Task | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);
  const [menu, setMenu] = useState(false);
  const profile = profiles.find(p => p.id === profileId) || profiles[0];

  useEffect(() => {
    let alive = true;
    api<{ initialized: boolean }>('/setup').then(d => { if (alive) setSetup(d.initialized); }).catch(e => { if (alive) setError(e); });
    api<{ user: User; csrf_token: string }>('/session').then(d => { if (alive) { currentUser.current = d.user.id; setCsrf(d.csrf_token); setUser(d.user); } }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const refresh = useCallback(async () => {
    if (!user) return;
    const epoch = ++refreshEpoch.current;
    const results = await Promise.allSettled([
      api<Health>('/health'), api<{ models: Model[] }>('/models'), api<{ tasks: Task[] }>('/tasks'), api<{ profiles: Profile[]; active_profiles?: Record<string, Profile> }>('/profiles'),
    ]);
    if (currentUser.current !== user.id || epoch !== refreshEpoch.current) return;
    if (results[0].status === 'fulfilled') {
      setHealth(results[0].value); setHistory(h => [...h, (results[0] as PromiseFulfilledResult<Health>).value].slice(-12)); setStale(false);
    } else { setStale(true); setError(results[0].reason); }
    if (results[1].status === 'fulfilled') setModels(results[1].value.models);
    if (results[2].status === 'fulfilled') setTasks(results[2].value.tasks);
    if (results[3].status === 'fulfilled') { setProfiles(results[3].value.profiles); setActiveProfiles(results[3].value.active_profiles || {}); }
  }, [user]);

  useEffect(() => {
    if (!user) return;
    api<{ profiles: Profile[]; active_profiles?: Record<string, Profile> }>('/profiles').then(d => { if (currentUser.current === user.id) { setProfiles(d.profiles); setActiveProfiles(d.active_profiles || {}); } }).catch(e => { if (currentUser.current === user.id) setError(e); });
    api<{ samples: Sample[] }>('/samples').then(d => { if (currentUser.current === user.id) setSamples(d.samples); }).catch(e => { if (currentUser.current === user.id) setError(e); });
    void refresh();
    const timer = setInterval(() => void refresh(), 8000);
    return () => clearInterval(timer);
  }, [user, refresh]);

  useEffect(() => {
    if (!selected || terminal(selected.status)) return;
    let alive = true;
    const timer = setInterval(() => {
      api<Task>('/tasks/' + selected.id).then(d => { if (!alive) return; setSelected(d); if (terminal(d.status)) void refresh(); }).catch(e => { if (alive) setError(e); });
    }, 700);
    return () => { alive = false; clearInterval(timer); };
  }, [selected, refresh]);

  useEffect(() => {
    if (!selected) return;
    const previous = document.activeElement as HTMLElement | null;
    const drawer = document.querySelector<HTMLElement>('.drawer');
    const focusable = () => Array.from(drawer?.querySelectorAll<HTMLElement>('button:not(:disabled), input, select, textarea, summary, a[href]') || []);
    focusable()[0]?.focus();
    function keyboard(e: KeyboardEvent) {
      if (e.key === 'Escape') setSelected(null);
      if (e.key === 'Tab') {
        const elements = focusable();
        const first = elements[0], last = elements[elements.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
      }
    }
    document.addEventListener('keydown', keyboard);
    return () => { document.removeEventListener('keydown', keyboard); if (previous?.isConnected) previous.focus(); };
  }, [selected?.id]);

  function login(u: User, csrf: string) { currentUser.current = u.id; setCsrf(csrf); setUser(u); setPage('overview'); setSetup(true); setError(null); }
  async function logout() {
    try { await api('/session', 'DELETE'); }
    catch (e) { if (!(e instanceof ApiError && e.code === 'AUTH_REQUIRED')) { setError(e as Error); return; } }
    currentUser.current = ''; setCsrf(''); setUser(null); setPage('overview'); setSelected(null); setTasks([]); setHealth(null); setHistory([]); setProfiles([]); setActiveProfiles({}); setModels([]); setError(null);
  }
  function navigate(p: Page) { setPage(p); setMenu(false); setError(null); void refresh(); }
  async function control(path: string) {
    setBusy(true); setError(null);
    try { const task = await api<Task>(path, 'POST'); setSelected(task); }
    catch (e) { setError(e as Error); } finally { setBusy(false); }
  }
  async function openTask(id: string) { try { setSelected(await api<Task>('/tasks/' + id)); } catch (e) { setError(e as Error); } }
  async function cancelTask(id: string) { try { setSelected(await api<Task>('/tasks/' + id + '/cancel', 'POST')); void refresh(); } catch (e) { setError(e as Error); } }
  if (!user) {
    if (setup === null) return <main className="initial-state"><div className="logo"><span>m</span> MODEL SERVICE</div><p role="status">連接本機控制台…</p><Notice error={error} /><button onClick={() => location.reload()}>重新連線</button></main>;
    return <Login initialized={setup} onLogin={login} />;
  }
  const engine = health?.engine;
  const active = selected && !terminal(selected.status);
  const titles: Record<Page, [string, string]> = {
    overview: ['服務總覽', '確認環境、選擇設定，開始第一筆可追溯的推論。'],
    models: ['模型與 Provider', '核對來源、授權與內容，再啟動自己的候選引擎。'],
    profiles: ['客製化設定', '同一核心、兩種政策。編輯、驗證、預覽與保存版本，正式服務由發布流程套用。'],
    playground: ['推論 Playground', '模型生成後通過格式、證據與安全驗證，才呈現正式結果。'],
    tasks: ['任務與佇列', '追蹤實際階段、等待時間與結果，不使用模擬進度。'],
    trace: ['Trace 與事件', '探索推論階段與操作紀錄，歷史事件保持唯讀。'],
    evaluations: ['評測與版本比較', '固定合成資料集、原始與安全層分開計分，逐筆追查差異。'],
    releases: ['發布與回滾', '檢查品質、獨立核准、真實驗證後套用；保留上一可用版本。'],
    monitoring: ['監控與本機告警', '真實請求、資源、版本與告警，可篩選鑽取及查看恢復。'],
    loads: ['負載與實驗追蹤', '固定本機資料、有限負載、安全停止及 MLflow 同步。'],
    backups: ['備份與隔離還原', '安全業務資料匯出，驗證相容性後建立新隔離安裝。'],
    quality: ['判讀錯誤與證據', '逐筆理解原始模型錯誤、政策差異及標註歧義，保留來源與舊分數。'],
    reviews: ['人工複核收件匣', '接受、修正、退回或資訊不足；人工結果另存，原始證據及模型發布不改寫。'],
    accounts: ['本機帳號與角色', '建立不同角色的帳號，後端持續執行操作授權。'],
  };
  return <div className={'app-shell ' + (profileId === 'asset' ? 'theme-asset' : 'theme-service')}>
    <a href="#main" className="skip-link">跳到主要內容</a>
    <aside className={'sidebar ' + (menu ? 'open' : '')}><div className="logo"><span>m</span> MODEL SERVICE</div><p className="sidebar-caption">可客製模型服務控制台</p><div className="nav-section">工作台</div><nav aria-label="產品導覽">{([
      ['overview', '◫', '服務總覽'], ['models', '◈', '模型與 Provider'], ['profiles', '⊞', '客製化設定'], ['playground', '▷', '推論 Playground'], ['evaluations', '▥', '評測與版本比較'], ['quality', '◈', '判讀錯誤與證據'], ['reviews', '☷', '人工複核收件匣'], ['releases', '⇄', '發布與回滾'], ['monitoring', '⌁', '監控與本機告警'], ['loads', '▥', '負載與實驗追蹤'], ['backups', '⊞', '備份與隔離還原'], ['tasks', '☷', '任務與佇列'], ['trace', '⌁', 'Trace 與事件'],
    ] as [Page, string, string][]).map(([id, icon, label]) => <button key={id} className={page === id ? 'active' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}><span aria-hidden="true">{icon}</span>{label}</button>)}</nav><div className="nav-section">後續切片</div><div className="planned-nav"><span>完整證據包</span></div>{user.role === 'admin' && <button className={'account-nav ' + (page === 'accounts' ? 'active' : '')} onClick={() => navigate('accounts')}>◎ 帳號與角色</button>}<div className="sidebar-bottom"><Badge text="本機候選沙盒" kind="dark" /><p>正式版本於發布頁查看。<br />Docker / Kubernetes 未實跑。</p></div></aside>
    <div className="workspace"><header className="topbar"><button className="mobile-menu" aria-label="切換導覽" aria-expanded={menu} onClick={() => setMenu(!menu)}>☰</button><label className="profile-switch"><span>目前設定</span><select aria-label="目前設定" value={profileId} onChange={e => { setProfileId(e.target.value); sessionStorage.setItem('profile', e.target.value); }}>{profiles.map(p => <option key={p.id} value={p.id}>{p.team}</option>)}</select></label><div className="topbar-right"><Badge text={stale ? '資料已過期' : engine?.ready ? '真實引擎就緒' : '引擎未就緒'} kind={stale ? 'warning' : engine?.ready ? 'teal' : ''} /><span className="user-label">{user.username}<small>{roleText[user.role]}</small></span><button className="text-button" onClick={logout}>登出</button></div></header>
      <main id="main" tabIndex={-1}><div className="page-heading"><div><p className="eyebrow">{profile?.name || '模型服務'} / {profile?.version || '載入設定中'}</p><h1>{titles[page][0]}</h1><p className="muted">{titles[page][1]}</p></div><button className="secondary" disabled={busy} onClick={async () => { setBusy(true); await refresh(); setBusy(false); }}>↻ 更新狀態</button></div><Notice error={error} onDismiss={() => setError(null)} />{stale && <div className="notice warning">連線失敗，以下保留最後取得的資料。請重新連線再判斷健康狀態。</div>}
      {!profile ? <Empty>正在載入設定與產品資料…</Empty> : <>
        {page === 'overview' && <Overview health={health} history={history} tasks={tasks} profile={profile} go={navigate} />}
        {page === 'models' && <Models models={models} user={user} engineReady={!!engine?.ready} busy={busy || !!active} control={control} />}
        {page === 'profiles' && <ConfigEditor profile={profile} user={user} changed={() => { void api<{ profiles: Profile[] }>('/profiles').then(d => setProfiles(d.profiles)); void refresh(); }} />}
        {page === 'profiles' && <AlertPolicyEditor profile={profile} user={user} />}
        {page === 'quality' && <><ClosureQualityStatus /><ErrorAnalysisExplorer /></>}
        {page === 'reviews' && <ReviewInbox profile={profile} user={user} />}
        {page === 'profiles' && <ReviewPolicyEditor profile={profile} user={user} />}
        {page === 'monitoring' && <MonitoringWorkbench user={user} openTask={openTask} />}
        {page === 'loads' && <LoadWorkbench profile={profile} user={user} openTask={openTask} />}
        {page === 'backups' && <><ReviewBackupWorkbench user={user} /><BackupWorkbench user={user} /></>}
        {page === 'evaluations' && <EvaluationWorkbench profile={profile} ready={!!engine?.ready && !stale} />}
        {page === 'releases' && <ReleaseWorkbench profile={profile} user={user} health={health} changed={() => void refresh()} />}
        {page === 'profiles' && <Profiles profiles={profiles} selectedId={profileId} choose={id => { setProfileId(id); sessionStorage.setItem('profile', id); }} />}
        {page === 'playground' && <Playground profile={profile} activeProfile={activeProfiles[profileId]} samples={samples} ready={!!engine?.ready && !stale} onTask={t => { setSelected(t); void refresh(); }} />}
        {page === 'tasks' && <Tasks tasks={tasks} open={openTask} cancel={cancelTask} user={user} />}
        {page === 'trace' && <Trace tasks={tasks} open={openTask} />}
        {page === 'accounts' && user.role === 'admin' && <Accounts />}
      </>}
      <footer className="page-footer"><span>0.4.1 · 單實例 · 安全驗證後呈現</span><span>{health ? '環境樣本：' + date(health.sampled_at) : '尚無環境樣本'}</span></footer></main>
    </div>{selected && <div className="drawer-backdrop" onClick={() => terminal(selected.status) && setSelected(null)}><section className="drawer" role="dialog" aria-modal="true" aria-label="任務詳情" onClick={e => e.stopPropagation()}><div className="drawer-heading"><h2>任務詳情</h2><button aria-label="關閉任務詳情" onClick={() => setSelected(null)}>×</button></div><TaskDetail task={selected} />{selected.progress && <p role="status">已完成 {selected.progress.completed}/{selected.progress.total} · {selected.progress.case_id}</p>}{selected.workflow_result != null && <pre>{JSON.stringify(selected.workflow_result, null, 2)}</pre>}<div className="drawer-actions">{selected.status === 'queued' && selected.kind === 'inference' && <button onClick={() => cancelTask(selected.id)}>取消等待工作</button>}<button className="secondary" onClick={() => { setSelected(null); navigate('tasks'); }}>前往任務列表</button></div></section></div>}
  </div>;
}

function Overview({ health, history, tasks, profile, go }: { health: Health | null; history: Health[]; tasks: Task[]; profile: Profile; go: (p: Page) => void }) {
  const [point, setPoint] = useState<number | null>(null);
  const selected = point !== null ? history[point] : null;
  const data = selected || health;
  return <><section className="welcome-card"><div><Badge text="01 / 模型到服務" kind="dark" /><h2>你的下一步，從真實推論開始。</h2><p>{profile.team} · {profile.focus}。<br />先核對模型並啟動候選引擎，再用合成案例檢視結果。</p><div className="button-row"><button className="white-button" onClick={() => go('models')}>管理模型 <span>↗</span></button><button className="ghost" onClick={() => go('playground')}>開啟 Playground →</button></div></div><div className="flow-card"><div><span>01</span><strong>選擇設定</strong><Badge text={profile.version} kind="dark" /></div><i>↓</i><div><span>02</span><strong>模型與推論</strong><Badge text={health?.engine.ready ? '可執行' : '待啟動'} kind="dark" /></div><i>↓</i><div><span>03</span><strong>驗證與證據</strong><Badge text="保留版本" kind="dark" /></div></div></section>
    <div className="metric-grid"><Metric label="可用記憶體" value={data ? `${data.ram_free_gib} GB` : '—'} hint={data ? `總量 ${data.ram_total_gib} GB · 即時樣本` : '尚無樣本'} /><Metric label="GPU 顯存使用" value={data?.gpu ? `${(data.gpu.used_mib / 1024).toFixed(2)} GB` : '—'} hint={data?.gpu ? `總量 ${(data.gpu.total_mib / 1024).toFixed(2)} GB` : '無可驗證 GPU 樣本'} /><Metric label="執行中 / 等待中" value={health ? `${health.queue.running ? 1 : 0} / ${health.queue.waiting}` : '—'} hint="單一容量槽 · 有限佇列" /><Metric label="可用磁碟" value={data ? `${data.disk_free_gib} GB` : '—'} hint="作品所在磁碟 · 實際查詢" /></div>
    <div className="two-column"><section className="panel"><div className="panel-heading"><h2>環境健康</h2><Badge text="真實探查" kind="teal" /></div><div className="health-rows"><HealthRow name="候選模型引擎" value={health ? statusText[health.engine.status] || health.engine.status : '尚未取得'} good={!!health?.engine.ready} /><HealthRow name="產品資料庫" value={health?.database === 'ready' ? '就緒' : '尚未取得'} good={health?.database === 'ready'} /><HealthRow name="GPU" value={health?.gpu?.name || '尚無樣本'} good={!!health?.gpu} /><HealthRow name="容器 / Kubernetes" value="未實跑" /><HealthRow name="MLflow / Prometheus" value="按需追蹤／指標輸出；無常駐伺服器" /></div>{health?.engine.reason && <p className="inline-error">{errorMessage(health.engine.reason)}</p>}<button className="text-button" onClick={() => go('models')}>查看模型與修復入口 →</button></section><section className="panel"><div className="panel-heading"><h2>記憶體觀察</h2><span className="muted">本次瀏覽期間 · 最多 12 筆</span></div>{history.length < 2 ? <Empty>取得兩筆實際樣本後呈現趨勢，不補造零值。</Empty> : <><svg className="resource-chart" viewBox="0 0 500 120" role="img" aria-label="可用 RAM 趨勢"><title>可用 RAM，各資料點對應下方樣本表</title><line x1="0" y1="110" x2="500" y2="110" className="chart-grid" /><polyline className="chart-line" points={history.map((h, i) => `${i * 500 / Math.max(history.length - 1, 1)},${110 - h.ram_free_gib / h.ram_total_gib * 100}`).join(' ')} /></svg><div className="sample-buttons">{history.map((h, i) => <button key={h.sampled_at} aria-label={'查看環境樣本 ' + date(h.sampled_at)} aria-pressed={point === i} onClick={() => setPoint(point === i ? null : i)}>{i + 1}</button>)}</div><p className="muted">點樣本查看上方實際 RAM／VRAM 與磁碟值。</p></>}{selected && <small>已選樣本：{date(selected.sampled_at)}</small>}</section></div>
    <section className="panel"><div className="panel-heading"><h2>近期工作</h2><button className="text-button" onClick={() => go('tasks')}>查看全部 →</button></div>{tasks.length ? <div className="table-scroll"><table><thead><tr><th>任務</th><th>設定 / Provider</th><th>狀態</th><th>開始時間</th></tr></thead><tbody>{tasks.slice(0, 4).map(t => <tr key={t.id}><td><code>{t.id.slice(0, 8)}</code> · {kindLabel(t.kind)}</td><td>{t.profile_version || '管理操作'} / {t.provider_id || '本機'}</td><td><Badge text={statusText[t.status] || t.status} kind={t.status} /></td><td>{date(t.created_at)}</td></tr>)}</tbody></table></div> : <Empty>尚無工作。啟動模型或從 Playground 提交第一筆事件。</Empty>}</section>
  </>;
}
function Metric({ label, value, hint }: { label: string; value: string; hint: string }) { return <section className="metric"><p>{label}</p><strong>{value}</strong><small>{hint}</small></section>; }
function HealthRow({ name, value, good = false }: { name: string; value: string; good?: boolean }) { return <div><span>{name}</span><strong><i className={'status-dot ' + (good ? 'good' : '')} />{value}</strong></div>; }
function kindLabel(kind: string) { return ({ inference: '事件推論', verify_model: '模型雜湊驗證', start_engine: '啟動候選引擎', stop_engine: '停止候選引擎' } as Record<string, string>)[kind] || kind; }

function Models({ models, user, engineReady, busy, control }: { models: Model[]; user: User; engineReady: boolean; busy: boolean; control: (path: string) => void }) {
  return <><div className="notice info">這裡管理候選沙盒。模型來源已匯入，尚未經新產品的固定評測與發布核准。</div><div className="model-grid">{models.map(m => <section className="panel model-card" key={m.id}><div className="model-card-top"><span className="model-icon">{m.id === 'q8' ? '◈' : '⊡'}</span><Badge text={m.id === 'q8' ? '真實本機模型' : 'Fixture · 工程測試'} kind={m.id === 'q8' ? 'teal' : 'warning'} /></div><h2>{m.name}</h2><p className="muted">{m.id === 'q8' ? '設備事件分流 · 唯讀來源的独立副本' : '確定性合成結果，僅驗證流程與政策分支。'}</p><dl className="details"><dt>模型版本</dt><dd><code>{m.version}</code></dd><dt>發布狀態</dt><dd>{m.id === 'q8' ? '待評測 · 未發布' : '僅沙盒 · 禁止正式發布'}</dd><dt>串流</dt><dd>本切片不提供；TTFT 不量測</dd>{m.id === 'q8' && <><dt>授權 / 大小</dt><dd>{m.license} / {(m.bytes! / 2 ** 30).toFixed(2)} GiB</dd><dt>本機產物</dt><dd>{m.artifact_present ? '檔案存在，仍需內容驗證' : '缺少模型副本'}</dd><dt>最近完整驗證</dt><dd>{m.verification ? `${date(m.verification.verified_at)} · ${m.verification.file_count} 個檔案` : '尚未由產品完成驗證'}</dd><dt>SHA-256</dt><dd><code className="break">{m.sha256}</code></dd><dt>Base revision</dt><dd><code className="break">{m.base_revision}</code></dd></>}</dl>{m.id === 'q8' ? <>{user.role === 'admin' ? <div className="button-row"><button className="secondary" disabled={busy} onClick={() => control('/models/q8/verify')}>驗證模型與引擎</button><button className="primary" disabled={busy || engineReady} onClick={() => control('/engine/start')}>{engineReady ? '引擎已就緒' : '啟動候選引擎'}</button>{engineReady && <button disabled={busy} onClick={() => { if (window.confirm('停止候選引擎後，真實推論將不可用。確認停止？')) control('/engine/stop'); }}>停止引擎</button>}</div> : <p className="muted">啟動與驗證需要管理者；就緒後可在 Playground 選用。</p>}<details><summary>來源與歷史證據限制</summary><ul>{m.lineage_limitations?.map(x => <li key={x}>{x}</li>)}</ul><p>舊訓練文件保持原樣；匯入不代表重新訓練或通過新產品閘門。</p></details></> : <div className="notice warning">Fixture 成績不能代表模型品質或真實效能。請在 Playground 主動選用。</div>}</section>)}</div></>;
}

function Profiles({ profiles, selectedId, choose }: { profiles: Profile[]; selectedId: string; choose: (id: string) => void }) {
  return <><div className="notice info">以下為兩組目前候選摘要。表單、監控政策及保存入口在上方；正式服務仍以發布頁版本為準。</div><div className="two-column">{profiles.map(p => <section className="panel profile-card" key={p.id}><p className="eyebrow">{p.team}</p><h2>{p.name}</h2><p>{p.focus}</p><div className="tags"><Badge text={p.version} kind="teal" /><Badge text={p.prompt_version} /></div><dl className="details"><dt>任務</dt><dd>同一設備事件分流</dd><dt>必填欄位</dt><dd>{p.required_fields.map(fieldLabel).join('、')}</dd><dt>強制複核</dt><dd>{p.review_severities.map(x => severityText[x]).join('、')}{p.review_critical_assets ? '；關鍵設備所有事件' : ''}</dd><dt>發布核准政策</dt><dd>{p.approvals_required} 位非提案者複核{p.maintenance_window_required ? '，且限制維護時段' : ''}（由發布流程執行）</dd><dt>等待 / 生成逾時</dt><dd>{p.queue_timeout_seconds}s / {p.generation_timeout_seconds}s · 試跑預設</dd><dt>等待上限</dt><dd>{p.queue_limit} 筆 · 共用一個引擎容量槽</dd></dl><button className={p.id === selectedId ? 'secondary' : 'primary'} disabled={p.id === selectedId} onClick={() => choose(p.id)}>{p.id === selectedId ? '目前使用' : '選擇此設定'}</button></section>)}</div><section className="panel"><h2>同一事件，實際差異</h2><div className="comparison-flow"><div><span className="step-number">01</span><strong>輸入驗證</strong><p>設備版缺重要性或維護狀態時拒絕；維修版可提交。</p></div><div><span className="step-number">02</span><strong>結果複核</strong><p>相同有效 medium 結果，設備版需要複核；維修版無其他風險時可完成。</p></div><div><span className="step-number">03</span><strong>版本固定</strong><p>切換後舊任務的設定、Prompt 與安全政策版本保持不變。</p></div></div><p className="muted">Profile 共用同一安裝的資料，不宣稱多租戶或工作區隔離。安全底線不可降低。</p></section></>;
}
function fieldLabel(value: string) { return ({ incident_text: '事件文字', asset_id: '設備編號', asset_criticality: '設備重要性', maintenance_window: '維護時段狀態' } as Record<string, string>)[value] || value; }

export function Playground({ profile: candidate, activeProfile, samples, ready, onTask }: { profile: Profile; activeProfile?: Profile; samples: Sample[]; ready: boolean; onTask: (t: Task) => void }) {
  const [provider, setProvider] = useState('q8');
  const [mode, setMode] = useState('auto');
  const profile = mode === 'auto' && provider === 'q8' && activeProfile ? activeProfile : candidate;
  const [text, setText] = useState('');
  const [asset, setAsset] = useState('DEMO-PUMP-07');
  const [criticality, setCriticality] = useState('');
  const [windowState, setWindowState] = useState('');
  const [sampleId, setSampleId] = useState('');
  const [reviewRetention, setReviewRetention] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [last, setLast] = useState<Task | null>(null);
  useEffect(() => { setError(null); setReviewRetention(false); }, [profile.id]);
  function loadSample(id: string) {
    const s = samples.find(s => s.id === id); setSampleId(id);
    if (s) { setText(s.incident_text); setAsset(s.asset_id); setCriticality(s.asset_criticality); setWindowState(s.maintenance_window); }
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const task = await api<Task>('/inferences', 'POST', { profile_id: profile.id, provider_id: provider, mode, incident_text: text, asset_id: asset, asset_criticality: criticality || null, maintenance_window: windowState || null, sample_id: sampleId || null, review_retention: reviewRetention ? 'local_review_7_days' : 'memory_only' }, { 'Idempotency-Key': crypto.randomUUID() });
      setLast(task); onTask(task);
    } catch (e) { setError(e as Error); } finally { setBusy(false); }
  }
  const needsAsset = profile.required_fields.includes('asset_criticality');
  const needsWindow = profile.required_fields.includes('maintenance_window');
  return <><div className={'notice ' + (provider === 'fixture' ? 'warning' : 'info')}>{provider === 'fixture' ? 'Fixture 測試：輸出與速度不代表真實模型。這筆工作禁止正式發布。' : '自動模式使用後端已發布版本（若存在）；沙盒模式使用目前候選。結果附真實設定與發布 ID。'}</div><div className="playground-grid"><section className="panel"><div className="panel-heading"><h2>輸入{profile.noun}</h2><Badge text={profile.version} kind="teal" /></div><Notice error={error} /><form onSubmit={submit}><div className="form-row"><label>Provider<select aria-label="Provider" value={provider} onChange={e => setProvider(e.target.value)}><option value="q8">Q8 · 真實本機模型</option><option value="fixture">Fixture · 確定性測試</option></select></label><label>合成範例<select aria-label="合成範例" value={sampleId} onChange={e => loadSample(e.target.value)}><option value="">自行輸入</option>{samples.map(s => <option value={s.id} key={s.id}>{s.title}</option>)}</select></label></div><label>執行版本<select aria-label="執行版本" value={mode} onChange={e => setMode(e.target.value)}><option value="auto">自動使用已發布版本；尚無版本時使用候選</option><option value="sandbox">明示沙盒 · 使用目前候選</option></select></label><label>設備編號<input value={asset} onChange={e => { setAsset(e.target.value); setSampleId(''); }} required pattern="[A-Za-z0-9_-]{3,64}" maxLength={64} /></label><div className="form-row"><label>設備重要性 {needsAsset ? '＊' : '（選填）'}<select aria-label="設備重要性" value={criticality} required={needsAsset} onChange={e => { setCriticality(e.target.value); setSampleId(''); }}><option value="">請選擇</option><option value="standard">一般設備</option><option value="critical">關鍵設備</option></select></label><label>維護時段 {needsWindow ? '＊' : '（選填）'}<select aria-label="維護時段" value={windowState} required={needsWindow} onChange={e => { setWindowState(e.target.value); setSampleId(''); }}><option value="">請選擇</option><option value="available">時段可用</option><option value="unavailable">時段不可用</option></select></label></div><label>事件文字<textarea aria-label="事件文字" value={text} onChange={e => { setText(e.target.value); setSampleId(''); }} required minLength={8} maxLength={1600} rows={6} placeholder="描述設備異常、發生条件與已知量測資訊…" /></label><div className="input-meta"><small>{text.length} / 1600 字元</small><small>{sampleId ? '合成樣本可保存結果' : reviewRetention ? '自願本機複核證據保存7日' : '原文不落盤；結果暫留 15 分鐘'}</small></div>{provider === 'q8' && !ready && <p className="inline-error">真實引擎尚未就緒。請由管理者啟動，或主動選擇 Fixture。</p>}<label className="check"><input aria-label="本機複核證據保存同意" type="checkbox" checked={reviewRetention} onChange={e => setReviewRetention(e.target.checked)} />自願保留需要複核的原文／模型證據於本機7日，跨重啟可複核；不納入備份或公開匯出</label><button className="primary full" disabled={busy || provider === 'q8' && !ready}>{busy ? '提交中…' : '執行並驗證'}<span>↗</span></button></form></section><aside><section className="panel"><p className="eyebrow">本次規則</p><h2>結果需要如何複核？</h2><p className="notice info">本次執行設定：{profile.version} · {mode === "auto" && provider === "q8" && activeProfile ? "已發布版本" : "沙盒候選"}</p><ul className="rule-list"><li>高風險、未知、注入與缺證據必須複核。</li><li>{profile.review_severities.includes('medium') ? '中風險也需複核。' : '中風險且無其他安全問題時可完成。'}</li><li>{profile.review_critical_assets ? '關鍵設備全部事件需複核。' : '設備重要性不額外增加複核條件。'}</li></ul><hr /><p className="muted">結果的設備風險複核需求保持記錄；模型服務發布另由獨立複核者核准，不等同設備操作批准。</p></section><section className="panel"><h2>版本與證據</h2><p className="muted">任務綁定啟動時設定。右側詳情可查看正式結果、原始模型結果、安全介入與階段時間。</p>{last ? <button className="secondary" onClick={() => api<Task>('/tasks/' + last.id).then(onTask).catch(e => setError(e as Error))}>查看最近任務 {last.id.slice(0, 8)}</button> : <Empty>執行後會建立真實任務與 Trace。</Empty>}</section></aside></div></>;
}

function Tasks({ tasks, open, cancel, user }: { tasks: Task[]; open: (id: string) => void; cancel: (id: string) => void; user: User }) {
  const [filter, setFilter] = useState('all');
  const [profileFilter, setProfileFilter] = useState('all');
  const visible = tasks.filter(t => (filter === 'all' || t.status === filter) && (profileFilter === 'all' || t.profile_id === profileFilter));
  return <section className="panel"><div className="panel-heading"><h2>實際任務紀錄</h2><div className="filters"><select aria-label="任務狀態篩選" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部狀態</option>{Object.entries(statusText).slice(0, 7).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select><select aria-label="任務設定篩選" value={profileFilter} onChange={e => setProfileFilter(e.target.value)}><option value="all">全部設定</option><option value="service">維修團隊</option><option value="asset">設備團隊</option></select></div></div>{!visible.length ? <Empty>目前篩選沒有任務。調整條件或從 Playground 建立工作。</Empty> : <div className="table-scroll"><table><thead><tr><th>任務 / 種類</th><th>設定版本</th><th>證據類型</th><th>狀態</th><th>等待 / 生成</th><th>操作</th></tr></thead><tbody>{visible.map(t => <tr key={t.id}><td><button className="text-button" onClick={() => open(t.id)}>{t.id.slice(0, 8)} ↗</button><small>{kindLabel(t.kind)}</small></td><td>{t.profile_version || '管理操作'}</td><td><Badge text={t.evidence_kind === 'fixture' ? 'Fixture' : t.evidence_kind === 'measured' ? '真實模型' : '檔案 / 程序'} kind={t.evidence_kind === 'fixture' ? 'warning' : ''} /></td><td><Badge text={statusText[t.status] || t.status} kind={t.status} /></td><td>{t.queue_ms != null ? `${t.queue_ms.toFixed(0)}ms` : '—'} / {t.generation_ms != null ? `${(t.generation_ms / 1000).toFixed(2)}s` : '—'}</td><td>{t.status === 'queued' && t.kind === 'inference' && (user.id === t.owner || user.role === 'admin') ? <button className="text-button" onClick={() => cancel(t.id)}>取消等待</button> : <button className="text-button" onClick={() => open(t.id)}>詳情</button>}</td></tr>)}</tbody></table></div>}<p className="muted">執行中同步生成尚無安全取消能力；排隊工作可取消。服務重啟後中斷工作標示失敗，不自動重播。</p></section>;
}

function Trace({ tasks, open }: { tasks: Task[]; open: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [audit, setAudit] = useState<Audit[]>([]);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => { api<{ events: Audit[] }>('/audit').then(d => setAudit(d.events)).catch(e => setError(e as Error)); }, [tasks]);
  const visible = tasks.filter(t => (t.id + t.trace_id + t.profile_version + t.status).toLowerCase().includes(query.toLowerCase()));
  return <><label className="search-label">搜尋任務、Trace ID、設定版本或狀態<input value={query} onChange={e => setQuery(e.target.value)} placeholder="輸入 ID 或版本…" /></label><div className="two-column"><section className="panel"><h2>任務追溯</h2>{visible.length ? <div className="trace-list">{visible.map(t => <button key={t.id} onClick={() => open(t.id)}><div><strong>{kindLabel(t.kind)} · {t.id.slice(0, 8)}</strong><small>{t.profile_version || '管理操作'} · {date(t.created_at)}</small><code>{t.trace_id}</code></div><Badge text={statusText[t.status] || t.status} kind={t.status} /></button>)}</div> : <Empty>沒有符合條件的 Trace。</Empty>}</section><section className="panel"><h2>操作事件</h2><Notice error={error} />{audit.length ? <ol className="timeline">{audit.filter(a => !query || (a.action + a.target).includes(query)).slice(0, 30).map(a => <li key={a.id}><strong>{({ bootstrap: '初始化管理者', login: '登入', logout: '登出', inference_submitted: '提交推論', task_cancelled: '取消等待任務', role_assigned: '賦予角色', user_created: '建立帳號' } as Record<string, string>)[a.action] || kindLabel(a.action)}</strong><small>{date(a.time)}</small><code>目標 {a.target.slice(0, 12)}</code></li>)}</ol> : <Empty>尚無操作事件。</Empty>}<p className="muted">本機追加式紀錄，並非外部簽署或不可變稽核。</p></section></div></>;
}

export function TaskDetail({ task }: { task: Task }) {
  return <><div className="tags"><Badge text={statusText[task.status] || task.status} kind={task.status} /><Badge text={task.evidence_kind === 'fixture' ? 'Fixture · 非模型成績' : task.provider_id === 'q8' ? '真實本機模型' : '真實管理工作'} kind={task.evidence_kind === 'fixture' ? 'warning' : 'teal'} /></div>{task.release_id && <p className="notice info">已發布版本 · {task.profile_version} · Release {task.release_id.slice(0, 8)}</p>}<p className="muted">{kindLabel(task.kind)} · {date(task.created_at)}</p>{task.error && <Notice error={new ApiError(task.error)} />}{task.result ? <section className="result-block"><div className="result-head"><Badge text={eventText[task.result.event_type]} /><Badge text={'風險：' + severityText[task.result.severity]} kind={task.result.severity} /></div><h3>{task.result.summary_zh_tw}</h3><p className={task.result.review_required ? 'review-required' : 'review-clear'}>{task.result.review_required ? '需要人工複核 · 不可直接發布或執行設備動作' : '本次規則未要求額外複核 · 尚無發布資格'}</p><h4>原文證據</h4>{task.result.evidence_spans.length ? task.result.evidence_spans.map((s, i) => <blockquote key={i}>{s}</blockquote>) : <p>未找到有效原文證據。</p>}<h4>缺失資訊</h4><p>{task.result.missing_information.join('、') || '模型未列出缺失資訊'}</p><h4>安全分流代碼</h4><p>{task.result.safe_actions.map(x => actionText[x]).join('、')}</p><h4>安全介入</h4><p>{task.warnings?.join('、') || '沒有額外介入紀錄'}</p><details><summary>比較原始模型輸出與正式結果</summary><p className="muted">原始輸出尚未套用確定性安全政策，不可直接使用。</p><pre>{JSON.stringify(task.raw_output, null, 2)}</pre><p>正式結果</p><pre>{JSON.stringify(task.result, null, 2)}</pre></details></section> : terminal(task.status) && task.kind === 'inference' && task.status !== 'failed' ? <Empty>完整結果已到期或服務重啟後不再保留。中繼資料與狀態仍可追溯。</Empty> : !terminal(task.status) ? <div className="progress-state" role="status"><span className="pulse" />{statusText[task.status]}。此處顯示實際階段，沒有預估百分比。</div> : <p className="muted">管理工作已結束，請更新狀態查看最新模型健康。</p>}<dl className="details"><dt>任務 ID</dt><dd><code className="break">{task.id}</code></dd><dt>Trace ID</dt><dd><code className="break">{task.trace_id}</code></dd>{task.profile_version && <><dt>設定 / Prompt</dt><dd>{task.profile_version} / {task.prompt_version}</dd><dt>快照 SHA-256</dt><dd><code className="break">{task.config_snapshot_sha256}</code></dd><dt>模型版本</dt><dd><code className="break">{task.model_version}</code></dd><dt>保存規則</dt><dd>{task.input_retention === 'synthetic_sample' ? '原創合成樣本；結果保存於本機' : task.input_retention === 'local_review_opt_in' ? '自願本機複核證據保存7日；不纳入備份' : '原文不落盤；完整結果暫留記憶體 15 分鐘'}</dd><dt>排隊 / 生成</dt><dd>{task.queue_ms ?? '—'}ms / {task.generation_ms ?? '—'}ms</dd><dt>Token 使用</dt><dd>{task.generation?.usage.prompt_tokens ?? '—'} 輸入 / {task.generation?.usage.completion_tokens ?? '—'} 輸出</dd><dt>Token-level TTFT</dt><dd>未量測 · 本切片未請求串流</dd></>}</dl><h3>實際階段時間軸</h3><ol className="timeline">{task.events.map((e, i) => <li key={i}><strong>{statusText[e.stage] || e.stage}</strong><small>{date(e.time)}</small>{e.reason && <p>{errorMessage(e.reason)}</p>}</li>)}</ol></>;
}

function Accounts() {
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('operator');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [success, setSuccess] = useState('');
  const refresh = () => api<{ users: User[] }>('/users').then(d => setUsers(d.users)).catch(e => setError(e as Error));
  useEffect(() => { void refresh(); }, []);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null); setSuccess('');
    try { await api('/users', 'POST', { username, password, role }); setPassword(''); setSuccess('帳號已建立。請使用該帳號登入驗證角色。'); await refresh(); }
    catch (e) { setError(e as Error); } finally { setBusy(false); }
  }
  return <div className="two-column"><section className="panel"><h2>建立本機帳號</h2><Notice error={error} />{success && <div className="notice success" role="status">{success}</div>}<form onSubmit={submit}><label>新帳號<input autoComplete="off" value={username} onChange={e => setUsername(e.target.value)} required pattern="[a-z][a-z0-9_-]{2,31}" maxLength={32} /></label><label>新密碼<input autoComplete="new-password" type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={12} maxLength={128} /></label><label>角色<select aria-label="角色" value={role} onChange={e => setRole(e.target.value)}><option value="operator">一般使用者 · 推論與查看自己的任務</option><option value="reviewer">複核者 · 查看授權結果與證據</option><option value="admin">管理者 · 帳號與引擎控制</option></select></label><button className="primary" disabled={busy}>{busy ? '建立中…' : '建立帳號'}</button></form><p className="muted">本切片尚未提供帳號停用、密碼重設或企業 SSO。角色不是前端展示切換。</p></section><section className="panel"><h2>本機角色清單</h2>{users.length ? <ul className="user-list">{users.map(u => <li key={u.id}><strong>{u.username}</strong><Badge text={roleText[u.role]} /></li>)}</ul> : <Empty>尚未取得帳號清單。</Empty>}<p className="muted">密碼採 scrypt 雜湊，不回傳秘密值，不將 session 保存至瀏覽器儲存。</p></section></div>;
}
