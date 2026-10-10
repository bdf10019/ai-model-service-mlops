import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import evidence from './evidence-summary.json';
import './portfolio-guide.css';
import { repelTypography, useTextRepulsion } from './repellingText';

type Profile = 'service' | 'asset';
type Scenario = 'normal' | 'missing' | 'critical';
type Layer = 'raw' | 'post';

const chapters = [
  ['01', '作品首頁', 'home'], ['02', '使用情境', 'scenario'], ['03', '客製實驗台', 'customize'],
  ['04', '產品流程', 'flow'], ['05', '發布與回滾', 'release'], ['06', '人工複核', 'review'],
  ['07', '維運工作台', 'operations'], ['08', '成果與限制', 'evidence'], ['09', '開始使用', 'start'],
] as const;

const profiles = {
  service: {
    team: '維修服務團隊', product: '水泵維修分流台', noun: '水泵案件', accent: 'teal',
    summary: '快速收斂循環水泵的現場證據，讓維修案件進入可追查的處理流程。',
    fields: ['事件文字', '設備編號', '現場狀態'], review: 'high／critical／unknown；高風險一位複核者',
    approval: '一名獨立 reviewer，admin 執行', reason: '至少 8 字', alert: 'P95 2,000 ms',
    params: '320 tokens · seed 42', queue: '4 筆／10 秒',
  },
  asset: {
    team: '設備管理團隊', product: '水泵資產守望台', noun: '水泵事件', accent: 'indigo',
    summary: '對關鍵循環水泵加嚴工況、溫度來源、人工證據與發布核准。',
    fields: ['事件文字', '設備編號', '運轉工況', '軸承溫度來源', '設備重要性', '維護狀態'], review: '再加 medium 與關鍵設備；高風險兩位複核者',
    approval: '兩名不同 reviewer＋維護確認，admin 執行', reason: '至少 16 字＋證據參照', alert: 'P95 1,500 ms',
    params: '384 tokens · seed 84', queue: '2 筆／5 秒',
  },
} as const;

const scenarios = {
  normal: { label: '主案例', input: '循環水泵在高負載下持續震動，軸承溫度偏高；現場未發現洩漏、火花或焦味。', severity: '中', result: '轉人工', note: '偏高溫度需要查證，但「未發現洩漏、火花或焦味」不能被誤讀成已發生危害。' },
  missing: { label: '資訊不足', input: '循環水泵間歇震動，尚未提供負載、振動量測值與軸承溫度來源。', severity: '中', result: '轉人工', note: '缺少量測與工況時先補證據，不把轉人工直接算成模型錯誤。' },
  critical: { label: '高風險', input: '循環水泵馬達端子持續出現火花與焦味，設備尚未停機。', severity: '危急', result: '強制複核', note: '真實火花與焦味必須保留安全升級，不能由Profile降低。' },
} as const;

const flowSteps = [
  { id: '設定', role: 'operator／admin', output: '不可變設定版本＋SHA', text: '表單、Schema、預覽與差異；只影響後續新任務。' },
  { id: '推論', role: 'operator', output: 'raw、post、Trace', text: '真實Q8與Fixture明確區分，任務固定啟動版本。' },
  { id: '評測', role: 'operator', output: '固定報告＋分母', text: '品質、延遲、安全與人工負擔分開計算。' },
  { id: '核准', role: 'reviewer', output: 'exact bundle核准', text: '提案者不可自批，設備版需要兩名不同複核者。' },
  { id: '發布', role: 'admin', output: 'active／previous', text: '真實探針成功後才切換，失敗保留既有版本。' },
  { id: '監控', role: '授權角色', output: '指標、告警、Trace', text: 'HTTP、排隊、引擎時間分開，不冒充TTFT。' },
  { id: '回滾', role: 'admin', output: '新請求驗證', text: 'A→B→A是同一Q8的服務設定回滾。' },
];

const replay = [
  { title: '候選綁定', body: '設定、Q8權重、24筆回歸報告與程式契約形成候選。', status: '已建立歷史候選' },
  { title: '品質檢查', body: '自動案例正確率與不必要介入超出固定門檻。', status: '拒絕' },
  { title: '提案入口', body: '發布按鈕保持停用，沒有用人工結果補成模型合格。', status: '未建立提案' },
  { title: '可用版本', body: '既有triage-v4只維持回歸基線標示，v5／v5.1未發布。', status: '狀態未變' },
];

const screenshots = [
  ['console-overview.png', '服務總覽', '0.4.1 · 隔離合成資料 · 2026-09-15'],
  ['quality-workbench.png', '品質門檻拒絕', '0.4.1 · 真實Q8歷史回放'],
  ['review-inbox.png', '人工複核收件匣', '0.4.1 · 隔離Fixture資料'],
  ['verified-rollback.png', '服務設定回滾', '0.4.1 · 同一Q8 A→B→A'],
  ['operations-monitoring.png', '監控工作台', '0.3.0歷史實測 · 非即時'],
  ['review-lifecycle-backup.png', '複核備份與隔離還原', '0.4.1 · 隔離E2E資料'],
] as const;

function Mark({ children, kind = 'demo' }: { children: ReactNode; kind?: 'demo' | 'measured' | 'limit' }) {
  return <span className={`mode ${kind}`}>{children}</span>;
}

function Icon({ name }: { name: 'layers' | 'shield' | 'chart' | 'arrow' | 'check' }) {
  const paths = {
    layers: <><path d="m4 9 8-4 8 4-8 4-8-4Z"/><path d="m4 13 8 4 8-4M4 17l8 4 8-4"/></>,
    shield: <path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6l-7-3Zm-3 9 2 2 4-5"/>,
    chart: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></>,
    arrow: <><path d="M5 12h14M14 7l5 5-5 5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}

function SectionHead({ number, eyebrow, title, lead }: { number: string; eyebrow: string; title: string; lead: string }) {
  return repelTypography(<header className="section-head"><span className="section-number">{number}</span><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p className="section-lead">{lead}</p></div></header>);
}

function LatencyChart({ values, metric }: { values: number[]; metric: 'queue' | 'generation' }) {
  const maximum = metric === 'queue' ? 1000 : 1400;
  return <svg className="latency-bars" viewBox="0 0 520 210" role="img" aria-label={`${metric === 'queue' ? '排隊' : '生成'}時間圖，P50 ${values[0]}毫秒，P95 ${values[1]}毫秒`}>
    {[0, .25, .5, .75, 1].map(fraction => <g key={fraction}><line className="grid-line" x1={58 + fraction * 330} x2={58 + fraction * 330} y1="20" y2="164"/><text className="chart-tick" x={58 + fraction * 330} y="191" textAnchor="middle">{maximum * fraction}</text></g>)}
    {values.map((value, index) => <g key={index}><text className="chart-label" x="2" y={64 + index * 74}>P{index === 0 ? '50' : '95'}</text><rect className="bar-track" x="58" y={39 + index * 74} width="330" height="36" rx="5"/><rect className={index === 0 ? 'bar-p50' : 'bar-p95'} x="58" y={39 + index * 74} width={value / maximum * 330} height="36" rx="5"/><text className="chart-value" x="408" y={64 + index * 74}>{value}</text></g>)}
    <text className="chart-tick" x="408" y="191">毫秒 ms</text>
  </svg>;
}

function App() {
  useTextRepulsion();
  const query = new URLSearchParams(location.search);
  const initial = query.get('profile') === 'asset' ? 'asset' : 'service';
  const requestedScenario = query.get('scenario');
  const [profile, setProfile] = useState<Profile>(initial);
  const [menu, setMenu] = useState(false);
  const [scenario, setScenario] = useState<Scenario>(requestedScenario === 'missing' || requestedScenario === 'critical' ? requestedScenario : 'normal');
  const [activeFlow, setActiveFlow] = useState(0);
  const [replayStep, setReplayStep] = useState(0);
  const [criticality, setCriticality] = useState('');
  const [maintenance, setMaintenance] = useState('');
  const [temperatureSource, setTemperatureSource] = useState('');
  const [preview, setPreview] = useState('');
  const [share, setShare] = useState('');
  const [reviewState, setReviewState] = useState('等待處理');
  const [reviewMode, setReviewMode] = useState<'view' | 'correct'>('view');
  const [reason, setReason] = useState('');
  const [humanSummary, setHumanSummary] = useState('現場主管需確認後續安排');
  const [evidenceChecked, setEvidenceChecked] = useState(false);
  const [loadConcurrency, setLoadConcurrency] = useState(1);
  const [metric, setMetric] = useState<'queue' | 'generation'>('queue');
  const [dataset, setDataset] = useState(0);
  const [layer, setLayer] = useState<Layer>('raw');
  const [caseId, setCaseId] = useState('h03');
  const [modal, setModal] = useState<number | null>(null);
  const modalClose = useRef<HTMLButtonElement>(null);
  const profileInfo = profiles[profile];
  const currentDataset = evidence.quality.datasets[dataset];
  const currentCase = evidence.quality.cases.find(c => c.id === caseId)!;
  const load = evidence.load.runs.find(r => r.concurrency === loadConcurrency)!;

  useEffect(() => {
    document.documentElement.dataset.profile = profile;
  }, [profile]);
  useEffect(() => {
    if (modal !== null) modalClose.current?.focus();
  }, [modal]);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setModal(null); };
    window.addEventListener('keydown', close); return () => window.removeEventListener('keydown', close);
  }, []);

  function selectProfile(next: Profile) {
    setProfile(next); setPreview(''); setReviewState('等待處理'); setReviewMode('view');
    setCriticality(''); setMaintenance(''); setTemperatureSource('');
  }
  function validatePreview() {
    if (profile === 'asset' && (!temperatureSource || !criticality || !maintenance)) {
      setPreview('設備版還需要「軸承溫度來源」、「設備重要性」與「維護狀態」。欄位錯誤已阻擋預覽。');
      return;
    }
    setPreview(`${profileInfo.product}預覽完成：${scenario === 'critical' ? profileInfo.review : scenarios[scenario].result}。只更新本站狀態。`);
  }
  function sharePreview() {
    history.replaceState(null, '', `?profile=${profile}&scenario=${scenario}#customize`);
    setShare('非敏感展示狀態已寫入網址；沒有上傳或修改控制台。');
  }
  function review(action: string) {
    if (action === 'correct') { setReviewMode('correct'); setReviewState('填寫修正內容'); return; }
    if (action === 'accept' && profile === 'asset') { setReviewState(reviewState === '第一位複核者已確認' ? '兩位不同複核者已完成合成示範' : '第一位複核者已確認'); return; }
    setReviewState(action === 'accept' ? '已接受合成建議' : action === 'return' ? '已退回補充資料' : '已標記資訊不足');
  }
  function saveCorrection() {
    const minimum = profile === 'asset' ? 16 : 8;
    if (reason.trim().length < minimum || (profile === 'asset' && !evidenceChecked)) {
      setReviewState(profile === 'asset' ? '需要至少16字理由與證據參照' : '需要至少8字修正理由'); return;
    }
    setReviewState('人工結果已另存於本站示範狀態；原始與政策結果沒有覆寫'); setReviewMode('view');
  }
  const qualityValue = layer === 'raw' ? currentDataset.raw_task : currentDataset.post_task;
  const qualityPercent = Math.round(qualityValue.numerator / qualityValue.denominator * 100);
  const bars = useMemo(() => metric === 'queue'
    ? [load.queue_p50_ms, load.queue_p95_ms]
    : [load.generation_p50_ms, load.generation_p95_ms], [load, metric]);

  return repelTypography(<div className="site-shell">
    <a className="skip-link" href="#main">跳到主要內容</a>
    <header className="site-header">
      <a className="brand" href="#home" aria-label="AI模型服務作品首頁"><span className="brand-mark">M</span><span><strong>MODEL SERVICE</strong><small>Evidence-led AI delivery</small></span></a>
      <button className="menu-button" aria-expanded={menu} aria-controls="chapter-nav" onClick={() => setMenu(!menu)}>九章導覽 <span>{menu ? '×' : '＋'}</span></button>
      <nav id="chapter-nav" className={menu ? 'open' : ''} aria-label="九章導覽">
        {chapters.map(([n, label, id]) => <a key={id} href={`#${id}`} onClick={() => setMenu(false)}><span>{n}</span>{label}</a>)}
      </nav>
      <a className="header-cta" href="#start">如何體驗 <Icon name="arrow" /></a>
    </header>

    <main id="main">
        <aside className="portfolio-guide" aria-label="三作品分工">
          <p>同一台循環水泵，三個作品接力</p>
          <nav aria-label="三作品導覽">
            <a href="#home" aria-current="page"><strong>作品一：判斷</strong><span>判斷水泵異常與需要補查的資料</span></a>
            <a href="http://127.0.0.1:4179/?v=1.0.0"><strong>作品二：執行</strong><span>查資料，經人員核准後建立工單</span></a>
            <a href="http://127.0.0.1:4180/?v=1.1.0"><strong>作品三：整理文件</strong><span>核對維修單，保留修正與來源</span></a>
          </nav>
          <small>目前是作品一。各站獨立展示，點連結可依序觀看。</small>
        </aside>
      <section id="home" className="hero chapter">
        <aside className="glass-intro" aria-label="第一次認識這個產品">
          <div className="glass-intro-content">
            <span className="glass-intro-label">第一次接觸 AI？從這裡開始</span>
            <p className="glass-intro-title">水泵一直震動，<span>先判斷要不要交給人員確認。</span></p>
            <p className="glass-intro-description">輸入水泵的震動、溫度與現場狀況，查看風險與缺少的資料。這次案例會交給人員複核；接著到作品二查資料、安排維修，最後到作品三整理維修單。</p>
            <p className="glass-intro-note">下方可直接體驗操作示範，不需要懂程式；示範不會操作真實設備。</p>
          </div>
        </aside>
        <div className="hero-copy">
          <div className="mode-row"><Mark>離線互動示範</Mark><Mark kind="measured">歷史實測可追查</Mark></div>
          <p className="eyebrow">AI 模型服務與 MLOps · 產品工程版</p>
          <h1>讓 AI 的每一次交付，<em>可配置、可判斷、可回復。</em></h1>
          <p className="hero-lead">同一台循環水泵、兩種團隊工作方式：維修團隊快速分流，設備團隊加嚴工況、溫度證據與雙人核准。候選不合格會被攔下，發生退步能回到可用設定。</p>
          <div className="hero-actions"><a className="button primary" href="#scenario">先看水泵發生什麼事 <Icon name="arrow" /></a><a className="button secondary" href="#evidence">先看實測證據</a></div>
          <ul className="trust-list"><li><Icon name="check" /> 真實Q8與Fixture清楚分開</li><li><Icon name="check" /> 原始、政策、人工結果不混算</li><li><Icon name="check" /> 失敗時不顯示假成功</li></ul>
        </div>
        <div className="hero-product" aria-label="可客製產品桌">
          <div className="product-top"><span>本機候選沙盒</span><Mark>合成畫面</Mark></div>
          <button className="hero-screen" onClick={() => setModal(0)} aria-label="放大真實控制台總覽"><img src="/images/console-overview.png" alt="隔離測試環境的真實模型服務控制台"/><span>真實控制台 · 隔離示範資料 <Icon name="arrow"/></span></button>
          <div className="profile-tabs" role="group" aria-label="首頁團隊切換">
            {(['service', 'asset'] as Profile[]).map(p => <button aria-pressed={profile === p} key={p} onClick={() => selectProfile(p)}>{profiles[p].team}</button>)}
          </div>
          <div className="product-card"><span className="product-index">01 / {profileInfo.noun}</span><h3>{profileInfo.product}</h3><p>{profileInfo.summary}</p><div className="mini-flow"><span>設定</span><i>→</i><span>評測</span><i>→</i><span>{profile === 'service' ? '1人核准' : '2人核准'}</span><i>→</i><span>可回滾</span></div></div>
          <div className="product-metrics"><div><small>發布狀態</small><strong>候選封鎖</strong></div><div><small>政策後安全違規</small><strong>0 <span>／有限測試</span></strong></div><div><small>模型權重</small><strong>Q8 未變</strong></div></div>
        </div>
        <div className="proof-strip"><div><strong>97</strong><span>後端測試</span></div><div><strong>17</strong><span>前端測試</span></div><div><strong>19</strong><span>主產品 E2E</span></div><div><strong>3</strong><span>已觀測品質集</span></div><p>工程驗證不等於模型合格。<a href="#evidence">查看分母與限制 →</a></p></div>
      </section>

      <section id="scenario" className="chapter section-wrap">
        <SectionHead number="02" eyebrow="一筆事件，三種結果" title="模型不是答案機；它是可治理流程的一站。" lead="選擇合成情境，看證據、風險與角色如何改變後續。這裡不呼叫模型。" />
        <div className="scenario-layout">
          <div className="scenario-picker" role="group" aria-label="合成情境">
            {(Object.keys(scenarios) as Scenario[]).map(key => <button aria-pressed={scenario === key} key={key} onClick={() => {setScenario(key); setPreview('');}}><span>{scenarios[key].label}</span><small>{key === 'normal' ? '主案例與否定詞' : key === 'missing' ? '缺漏與人工' : '安全底線'}</small></button>)}
          </div>
          <article className="incident-card"><div className="card-label"><Mark>合成示範</Mark><span>DEMO-{scenario.toUpperCase()}</span></div><blockquote>「{scenarios[scenario].input}」</blockquote><div className="incident-outcome"><div><small>風險判斷</small><strong>{scenarios[scenario].severity}</strong></div><div><small>{profileInfo.team}</small><strong>{scenario === 'missing' && profile === 'asset' ? '加嚴複核' : scenarios[scenario].result}</strong></div></div><p>{scenarios[scenario].note}</p></article>
          <aside className="role-card"><p className="eyebrow">誰在什麼時候做決定</p><ol><li><span>operator</span>提交事件、查看結果</li><li><span>reviewer</span>處理不確定與高風險</li><li><span>admin</span>執行已核准的服務切換</li></ol><p className="quiet">角色示範不建立登入身分，也不執行設備動作。</p></aside>
        </div>
      </section>

      <section id="customize" className="chapter section-wrap lab-section">
        <SectionHead number="03" eyebrow="核心互動" title="換的不是顏色，而是工作方式。" lead="同一份事件在兩套版本化Profile中，會改變欄位、推論參數、複核、核准、排隊及告警。" />
        <div className="lab-grid">
          <div className="lab-controls">
            <div className="profile-tabs large" role="group" aria-label="實驗台團隊切換">{(['service', 'asset'] as Profile[]).map(p => <button aria-pressed={profile === p} key={p} onClick={() => selectProfile(p)}>{profiles[p].team}</button>)}</div>
            <label>事件文字<textarea value={scenarios[scenario].input} readOnly rows={3} /></label>
            <div className="field-row"><label>設備編號<input value="EQ-PUMP-001" readOnly /></label><label>運轉工況<input value={scenario === 'normal' ? '高負載' : scenario === 'critical' ? '異常未停機' : '待補資料'} readOnly /></label></div>
            {profile === 'service' && <label>現場狀態<input value={scenario === 'critical' ? '火花與焦味，尚未停機' : scenario === 'missing' ? '量測與條件待補' : '無洩漏、火花或焦味'} readOnly /></label>}
            {profile === 'asset' && <><label>軸承溫度來源<select aria-label="軸承溫度來源" value={temperatureSource} onChange={e => setTemperatureSource(e.target.value)}><option value="">請選擇</option><option>現場溫度計</option><option>設備感測器</option><option>人工口述，待查證</option></select></label><div className="field-row"><label>設備重要性<select aria-label="設備重要性" value={criticality} onChange={e => setCriticality(e.target.value)}><option value="">請選擇</option><option>標準</option><option>關鍵</option></select></label><label>維護狀態<select aria-label="維護狀態" value={maintenance} onChange={e => setMaintenance(e.target.value)}><option value="">請選擇</option><option>維護時段可用</option><option>目前不可維護</option></select></label></div></>}
            <div className="button-row"><button className="button primary" onClick={validatePreview}>驗證並預覽</button><button className="button secondary" onClick={() => {setTemperatureSource('');setCriticality('');setMaintenance('');setPreview('');setShare('');}}>還原預設</button><button className="text-action" onClick={sharePreview}>分享展示狀態</button></div>
            {preview && <p className={preview.includes('還需要') ? 'feedback error' : 'feedback success'} role="status">{preview}</p>}{share && <p className="feedback info" role="status">{share}</p>}
          </div>
          <div className="impact-panel">
            <div className="impact-head"><span className={`team-dot ${profileInfo.accent}`} /><div><p>目前Profile</p><h3>{profileInfo.product}</h3></div><Mark>只改本站狀態</Mark></div>
            <dl className="impact-list"><div><dt>必填欄位</dt><dd>{profileInfo.fields.join('、')}</dd></div><div><dt>生成參數</dt><dd>{profileInfo.params}</dd></div><div><dt>人工複核</dt><dd>{profileInfo.review}</dd></div><div><dt>修正理由</dt><dd>{profileInfo.reason}</dd></div><div><dt>發布核准</dt><dd>{profileInfo.approval}</dd></div><div><dt>有限佇列</dt><dd>{profileInfo.queue}</dd></div><div><dt>延遲告警</dt><dd>{profileInfo.alert}</dd></div></dl>
            <div className="planned"><span>網站視覺預覽</span><p>文字標誌與本頁布局是介紹站設計；控制台的Logo自訂與首頁自由布局仍在規劃中。</p></div>
          </div>
        </div>
      </section>

      <section id="flow" className="chapter section-wrap">
        <SectionHead number="04" eyebrow="責任邊界" title="從設定到恢復，每一步都有操作者與證據。" lead="點選節點查看輸入、責任與產物。服務切換與人工業務處理是兩條不同的核准鏈。" />
        <div className="flow-track" role="tablist" aria-label="產品流程">{flowSteps.map((step, i) => <button role="tab" aria-selected={i === activeFlow} key={step.id} onClick={() => setActiveFlow(i)}><span>{String(i + 1).padStart(2, '0')}</span>{step.id}</button>)}</div>
        <div className="flow-detail"><div><p className="eyebrow">目前節點</p><h3>{flowSteps[activeFlow].id}</h3><p>{flowSteps[activeFlow].text}</p></div><dl><div><dt>允許角色</dt><dd>{flowSteps[activeFlow].role}</dd></div><div><dt>留下證據</dt><dd>{flowSteps[activeFlow].output}</dd></div><div><dt>失敗行為</dt><dd>保留上一狀態，顯示原因與恢復入口</dd></div></dl></div>
        <div className="architecture"><div><Icon name="layers" /><strong>React控制台</strong><small>表單、圖表、狀態回饋</small></div><i>→</i><div><Icon name="shield" /><strong>FastAPI政策層</strong><small>Session、CSRF、角色、契約</small></div><i>→</i><div><Icon name="chart" /><strong>Q8／SQLite／MLflow</strong><small>引擎、版本、實驗參照</small></div></div>
      </section>

      <section id="release" className="chapter section-wrap dark-section">
        <SectionHead number="05" eyebrow="歷史實測回放" title="不合格就停下；可用版本才有回程。" lead="單步重播固定歷史，不執行評測、發布或回滾。相同Q8的A→B→A只代表服務設定切換。" />
        <div className="release-grid"><div className="gate-card"><div className="gate-ring"><span>未通過</span><strong>8</strong><small>項拒絕原因</small></div><div><p className="eyebrow">triage-v5.1 · 未發布</p><h3>{replay[replayStep].title}</h3><p>{replay[replayStep].body}</p><span className={`status-chip ${replayStep === 1 ? 'denied' : ''}`}>{replay[replayStep].status}</span></div></div><div className="replay-panel"><div className="replay-steps">{replay.map((step, i) => <button key={step.title} aria-current={i === replayStep ? 'step' : undefined} onClick={() => setReplayStep(i)}><span>{i < replayStep ? '✓' : i + 1}</span><small>{step.title}</small></button>)}</div><div className="button-row"><button className="button light" onClick={() => setReplayStep(Math.min(replay.length - 1, replayStep + 1))} disabled={replayStep === replay.length - 1}>重播下一步</button><button className="text-action light-text" onClick={() => setReplayStep(0)}>重新開始</button></div><p className="history-note"><Mark kind="measured">歷史實測</Mark> 介面顯示固定結果；沒有進度動畫冒充即時服務。</p></div></div>
        <div className="rollback-line"><div><span>A</span><strong>service設定 A</strong><small>真實Q8探針通過</small></div><i>→</i><div><span>B</span><strong>service設定 B</strong><small>新請求核對參數</small></div><i>→</i><div><span>A</span><strong>previous A</strong><small>回滾後再核對</small></div><p>模型權重保持相同</p></div>
      </section>

      <section id="review" className="chapter section-wrap">
        <SectionHead number="06" eyebrow="合成業務複核" title="保留模型說了什麼，也保留人為何修正。" lead="原始輸出不能覆寫。下方操作只存在目前瀏覽器記憶體，不寫入控制台，也不計入模型正確率。" />
        <div className="review-top"><div className="profile-tabs compact" role="group" aria-label="複核團隊切換">{(['service','asset'] as Profile[]).map(p => <button aria-pressed={profile === p} key={p} onClick={() => selectProfile(p)}>{profiles[p].team}</button>)}</div><Mark>合成示範 · 不儲存</Mark></div>
        <p className="quiet">以下示範同一台水泵的高風險分支：馬達端子出現火花與焦味。這與主案例的「未發現危害」是兩種情境。</p>
        <div className="triage-columns"><article><span className="column-tag">01 模型原始</span><h3>電氣異常 · 中</h3><p>水泵馬達端子狀態需確認。</p><small>模型漏掉必要複核</small></article><article><span className="column-tag policy">02 政策後</span><h3>電氣異常 · 危急</h3><p>水泵馬達出現火花與焦味，交由人員依現場程序處理。</p><small>安全規則要求人工複核</small></article><article><span className="column-tag human">03 人工結果</span><h3>{reviewState}</h3><p>{reviewState.includes('另存') ? humanSummary : '等待符合角色的人員留下決定與理由。'}</p><small>永不回寫模型分數</small></article></div>
        <div className="review-actions"><div><h3>{profileInfo.team}處理規則</h3><p>{profileInfo.review}。理由{profileInfo.reason}。</p><div className="button-row"><button onClick={() => review('accept')} className="button primary">{profile === 'asset' && reviewState === '第一位複核者已確認' ? '第二位複核者確認' : '接受建議'}</button><button onClick={() => review('correct')} className="button secondary">修正允許欄位</button><button onClick={() => review('return')} className="text-action">退回</button><button onClick={() => review('insufficient')} className="text-action">資訊不足</button></div></div>{reviewMode === 'correct' && <div className="correction-form"><label>人工業務摘要<input aria-label="人工業務摘要" value={humanSummary} onChange={e => setHumanSummary(e.target.value)} /></label><label>修正理由<textarea aria-label="修正理由" rows={2} value={reason} onChange={e => setReason(e.target.value)} /></label>{profile === 'asset' && <label className="check"><input type="checkbox" checked={evidenceChecked} onChange={e => setEvidenceChecked(e.target.checked)} />我已選取逐字證據參照</label>}<button className="button primary" onClick={saveCorrection}>另存人工結果</button></div>}</div>
        <p className="feedback info" role="status">目前示範狀態：{reviewState}</p>
        <div className="retention"><Icon name="shield" /><div><strong>原始證據有期限</strong><p>一般來源記憶體15分鐘；明確同意的待複核來源本機7日。保存期間不可覆寫，不代表永久保存。</p></div><div><strong>人工內容另列</strong><p>摘要、理由與歷史可能含私人資料；備份前需admin預覽及明確確認。</p></div></div>
      </section>

      <section id="operations" className="chapter section-wrap ops-section">
        <SectionHead number="07" eyebrow="歷史維運工作台" title="吞吐變快時，排隊可能正在變長。" lead="比較兩組各六筆的有限本機量測。選項只切換已保存數據，不對任何服務送出流量。" />
        <div className="ops-controls"><div className="profile-tabs compact" role="group" aria-label="歷史負載併發"><button aria-pressed={loadConcurrency === 1} onClick={() => setLoadConcurrency(1)}>併發 1</button><button aria-pressed={loadConcurrency === 2} onClick={() => setLoadConcurrency(2)}>併發 2</button></div><select aria-label="維運指標" value={metric} onChange={e => setMetric(e.target.value as 'queue' | 'generation')}><option value="queue">排隊時間</option><option value="generation">引擎生成</option></select><Mark kind="measured">2026-09-14 · 真實Q8</Mark></div>
        <div className="ops-grid"><div className="ops-chart"><LatencyChart values={bars} metric={metric}/><p>{metric === 'queue' ? '排隊' : '生成'} P50 <strong>{bars[0]} ms</strong> · P95 <strong>{bars[1]} ms</strong></p></div><div className="ops-metrics"><div><small>成功</small><strong>{load.success}/{load.requests}</strong></div><div><small>吞吐</small><strong>{load.throughput_rps.toFixed(2)} rps</strong></div><div><small>排隊P95</small><strong>{load.queue_p95_ms} ms</strong></div><div><small>客戶端P95</small><strong>{load.client_turnaround_p95_ms} ms</strong></div><p>TTFT：未提供。完整生成時間沒有冒充逐Token首字時間。</p></div></div>
        <details className="trace-demo"><summary>鑽取量測條件與Trace摘要</summary><div className="trace-row"><span>load-{loadConcurrency}-01</span><span>HTTP接受</span><span>有限佇列</span><span>Q8生成</span><span>完成</span></div><p>1次預熱＋6筆量測；單機、單引擎。這是小樣本比較，不是SLO。</p></details>
      </section>

      <section id="evidence" className="chapter section-wrap evidence-section">
        <SectionHead number="08" eyebrow="可核對的成果與限制" title="分數之外，先看資料、分母與退步。" lead="所有數字來自白名單投影。這三組資料都已觀測，不能再稱未見保留驗證。" />
        <div className="evidence-toolbar"><div className="dataset-tabs" role="tablist" aria-label="品質資料版本">{evidence.quality.datasets.map((d,i) => <button role="tab" aria-selected={dataset === i} key={d.id} onClick={() => setDataset(i)}>{d.id.replace('equipment-','')}</button>)}</div><div className="profile-tabs compact" role="group" aria-label="結果層次"><button aria-pressed={layer === 'raw'} onClick={() => setLayer('raw')}>模型原始</button><button aria-pressed={layer === 'post'} onClick={() => setLayer('post')}>政策後</button></div></div>
        <div className="evidence-grid"><div className="quality-card"><div className="quality-score"><strong>{qualityPercent}%</strong><span>任務正確</span></div><div className="quality-bar"><i style={{width:`${qualityPercent}%`}} /></div><p>{qualityValue.numerator}/{qualityValue.denominator} · {layer === 'raw' ? '真實Q8收到版本化提示後的原始結果' : '不可降低安全政策後結果'}</p><dl><div><dt>有效生成</dt><dd>{currentDataset.generated_cases}/{currentDataset.total_cases}</dd></div><div><dt>安全違規</dt><dd>{layer === 'raw' ? currentDataset.raw_safety_violations : currentDataset.post_safety_violations}</dd></div><div><dt>生成P95</dt><dd>{currentDataset.generation_p95_ms} ms</dd></div><div><dt>TTFT</dt><dd>未提供</dd></div></dl></div><div className="burden-card"><h3>政策後人工負擔</h3>{[['應轉人工召回',currentDataset.post_required_review_recall],['不必要介入',currentDataset.post_unnecessary_review_rate],['自動完成',currentDataset.post_automatic_completion_rate],['自動案例正確',currentDataset.post_automatic_accuracy]].map(([name,value]) => {const v=value as typeof currentDataset.post_automatic_accuracy; return <button key={name as string} onClick={() => setCaseId(dataset === 2 ? 'h15' : 'h03')}><span>{name as string}</span><progress value={v.numerator} max={v.denominator}/><strong>{v.numerator}/{v.denominator}</strong></button>})}<p>人工修正完全不計入上述模型品質。</p></div></div>
        <div className="case-explorer"><div><p className="eyebrow">逐筆修正與退步</p><div className="case-tabs">{evidence.quality.cases.map(c => <button aria-pressed={caseId===c.id} key={c.id} onClick={() => setCaseId(c.id)}>{c.id}{c.change.post_regressed || c.change.raw_regressed ? ' ↓' : ' ✓'}</button>)}</div></div><article><div className="card-label"><strong>{currentCase.id}</strong><span>{currentCase.change.post_regressed || currentCase.change.raw_regressed ? '已知退步' : '已知缺陷修正證據'}</span></div><blockquote>「{currentCase.input}」</blockquote><div className="case-compare"><div><small>模型原始</small><strong>{currentCase.raw.event_type} · {currentCase.raw.severity}</strong><span>review {String(currentCase.raw.review_required)}</span></div><Icon name="arrow"/><div><small>政策後</small><strong>{currentCase.post.event_type} · {currentCase.post.severity}</strong><span>review {String(currentCase.post.review_required)}</span></div></div><p>固定期望：{currentCase.expected.event_type} · {currentCase.expected.severity}。{currentCase.id === 'h15' ? '新版原始輸出把unknown判成process，退步保留。' : '否定／反事實誤升級已在政策後修正。'}</p></article></div>
        <div className="capability-list"><div><strong>已完成</strong><p>本機端到端旅程、版本化客製、真實Q8評測與服務設定回滾、監控、複核、MLflow實驗同步、隔離還原。</p></div><div><strong>品質限制</strong><p>v5／v5.1未發布；人工介入偏高；沒有獨立專家或工廠泛化證據。</p></div><div><strong>尚未完成</strong><p>Model Registry、真正串流、相容端點、不同權重、Docker／Prometheus服務端及公開部署。</p></div></div>
        <div className="screens"><div className="screens-head"><h3>真實產品畫面</h3><p>隔離測試帳號與合成資料；點圖可放大。</p></div><div className="screen-grid">{screenshots.map((shot,i) => <button key={shot[0]} onClick={() => setModal(i)}><img src={`/images/${shot[0]}`} alt={`${shot[1]}控制台畫面`} loading="lazy"/><span><strong>{shot[1]}</strong><small>{shot[2]}</small></span></button>)}</div></div>
        <details className="limitations" open><summary>這些數字不能證明什麼？</summary><ul>{evidence.limitations.map(item => <li key={item}>{item}</li>)}</ul></details>
      </section>

      <section id="start" className="chapter section-wrap start-section">
        <SectionHead number="09" eyebrow="選擇閱讀深度" title="三分鐘看產品，六分鐘看證據。" lead="介紹站離線可用。真實控制台需要在訪客自己的電腦啟動並登入；本站不會自動探查。" />
        <div className="paths"><article><span>03 MIN</span><h3>產品導覽</h3><p>首頁 → 客製實驗台 → 發布回放 → 使用入口</p><a href="#customize" className="text-link">從客製化開始 <Icon name="arrow"/></a></article><article><span>06 MIN</span><h3>工程證據</h3><p>情境 → 客製 → 流程 → 發布 → 複核 → 維運 → 證據</p><a href="#scenario" className="text-link">從使用情境開始 <Icon name="arrow"/></a></article><article><span>LOCAL</span><h3>實際控制台</h3><p>需要產品0.4.1、使用者自己的帳號與本機服務。</p><a href="http://127.0.0.1:8120/" target="_blank" rel="noreferrer" className="text-link">本機已啟動：開啟控制台 <Icon name="arrow"/></a><a href="#launch-guide" className="text-link">尚未啟動：查看啟動方式 <Icon name="arrow"/></a></article></div>
        <div className="start-grid"><div className="launch-card" id="launch-guide"><p className="eyebrow">尚未啟動</p><h3>從作品目錄啟動</h3><pre><code>{`.\\scripts\\start.ps1\n# 然後開啟 http://127.0.0.1:8120`}</code></pre><p>127.0.0.1指向訪客自己的電腦；網站不自動探查或登入控制台。未登入的401不代表服務故障。</p></div><div className="download-card"><p className="eyebrow">作品摘要</p><h3>下載、列印、繼續閱讀</h3><a className="download-link" href="/downloads/作品摘要.md" download><span>Markdown作品摘要</span><small>版本、成果與限制</small></a><a className="download-link" href="/downloads/evidence-summary.json" download><span>JSON證據摘要</span><small>白名單、分母與來源SHA</small></a><button className="download-link" onClick={() => window.print()}><span>列印HTML作品摘要</span><small>使用瀏覽器另存PDF</small></button></div></div>
        <div className="final-status"><Mark kind="limit">誠實狀態</Mark><p><strong>本機階段可交付，不等於整份作品計畫完成。</strong>介紹站1.1.0只新增循環水泵展示客製化，不改變產品0.4.1、模型權重、歷史評測或候選發布資格。</p></div>
      </section>
    </main>
    <footer className="footer"><a className="brand small" href="#home"><span className="brand-mark">M</span><span><strong>MODEL SERVICE</strong><small>Showcase 1.1.0</small></span></a><p>繁體中文 · 隨附資產 · 無外部CDN · 不呼叫控制台API</p><a href="#home">返回頂部 ↑</a></footer>
    {modal !== null && <div className="modal-backdrop" role="presentation" onMouseDown={e => {if(e.target===e.currentTarget)setModal(null)}}><div className="image-modal" role="dialog" aria-modal="true" aria-label={screenshots[modal][1]}><button ref={modalClose} aria-label="關閉產品畫面" onClick={() => setModal(null)}>×</button><div><Mark kind="measured">真實產品畫面</Mark><h3>{screenshots[modal][1]}</h3><p>{screenshots[modal][2]}；測試帳號及資料均來自隔離安裝。</p></div><img src={`/images/${screenshots[modal][0]}`} alt={`${screenshots[modal][1]}放大畫面`}/></div></div>}
  </div>);
}

export default App;
