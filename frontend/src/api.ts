export type User = { id: string; username: string; role: 'admin' | 'reviewer' | 'operator' };
export type Profile = {
  id: string; version: string; name: string; team: string; accent: string; noun: string;
  focus: string; prompt_version: string; required_fields: string[]; review_severities: string[];
  review_critical_assets: boolean; approvals_required: number; maintenance_window_required: boolean;
  queue_limit: number; queue_timeout_seconds: number; generation_timeout_seconds: number;
  max_tokens: number; temperature: number; seed: number;
  quality_gate: { schema_rate: number; task_accuracy: number; raw_refusal_rate: number; max_generation_p95_ms: number } | null;
};
export type Sample = { id: string; title: string; incident_text: string; asset_id: string; asset_criticality: string; maintenance_window: string };
export type Engine = { status: string; reason: string | null; ready: boolean; runtime: string; context_tokens: number; concurrency: number };
export type Health = {
  ram_total_gib: number; ram_free_gib: number; disk_free_gib: number;
  gpu: { name: string; total_mib: number; used_mib: number } | null;
  sampled_at: number; engine: Engine; database: string;
  queue: { running: boolean; waiting: number; capacity: number; accepting: boolean };
  active_releases?: Record<string, string>; release_state?: { ready: boolean; error?: string };
};
export type Model = { id: string; name: string; evidence_kind: string; sha256: string | null; version: string; bytes?: number; license?: string; artifact_present?: boolean; verification?: { verified_at: number; file_count: number; manifest_sha256: string } | null; lineage_limitations?: string[]; base_revision?: string; engine?: Engine };
export type Result = { event_type: string; severity: string; summary_zh_tw: string; evidence_spans: string[]; missing_information: string[]; safe_actions: string[]; review_required: boolean; reason_codes: string[] };
export type Task = {
  id: string; trace_id: string; owner: string; kind: string; status: string; created_at: number;
  finished_at?: number; profile_id?: string; profile_version?: string; prompt_version?: string;
  provider_id?: string; evidence_kind?: string; model_version?: string; model_sha256?: string;
  config_snapshot_sha256?: string; input_retention?: string; sample_id?: string;
  result?: Result; raw_output?: Result; warnings?: string[];
  result_summary?: { event_type: string; severity: string; review_required: boolean };
  queue_ms?: number; generation_ms?: number; elapsed_ms?: number; raw_schema_valid?: boolean;
  generation?: { usage: { prompt_tokens?: number; completion_tokens?: number }; ttft_ms: number | null; streaming: string };
  error: string | null; events: { stage: string; time: number; reason?: string }[];
  release_id?: string | null; execution_mode?: string; report_sha256?: string;
  progress?: { completed: number; total: number; case_id: string }; workflow_result?: unknown;
};
export type Audit = { id: number; actor: string; action: string; target: string; time: number };

let csrf = '';
export function setCsrf(value: string) { csrf = value; }
const messages: Record<string, string> = {
  REVIEW_REVISION_CONFLICT: '複核資料版本已改變，草稿未套用；請明確重新載入後確認。', REVIEW_ALREADY_RESOLVED: '本筆已由其他處理形成最終结果，不能再次覆寫。', BUSINESS_REVIEW_ROLE_REQUIRED: '目前角色不符合此任務啟動時固定的業務複核政策。', REVIEW_INDEPENDENT_APPROVER_REQUIRED: '高風險第二次確認需要另一名不同複核者。', REVIEW_EVIDENCE_EXPIRED_OR_RESTARTED: '原始證據已到期／不可取得，不能處理；請重新提交推論。', REVIEW_SOURCE_CHANGED: '來源證據與固定摘要不符，已拒絕處理。', RESTORED_REVIEW_READ_ONLY_NO_EVIDENCE: '此筆從備份匯入，缺少原始證據，只能看業務歷史，不能重新批准。', REVIEW_BACKUP_FORMAT_HASH_OR_REFERENCE_INVALID: '複核備份格式、版本、SHA或歷史參照不符，未還原。', REVIEW_BACKUP_PREVIEW_EXPIRED: '預覽已過期或不屬於目前操作者，請重新預覽。', REVIEW_BACKUP_RECORD_LIMIT_64_PER_NAMESPACE: '本輪備份上限為每類64筆，超出時拒絕，不會漏存。', REVIEW_CORRECTION_INVALID: '修正欄位值不符合輸出Schema，尚未保存。', REVIEW_FIELD_NOT_ALLOWED: '此版本不允許修正該欄位。', REVIEW_REASON_REQUIRED: '處理理由未達任務固定政策字數或格式要求。', REVIEW_SAFETY_FLOOR_REQUIRED: '修正不得低於真實危害的安全底線。', REVIEW_EVIDENCE_REFERENCE_REQUIRED: '設備複核必須選擇逐字證據參照。', REVIEW_SECOND_APPROVAL_REQUIRED: '已有高風險提案，請由另一名複核者確認或退回。', REVIEW_RETENTION_CAPACITY: '本機複核證據已達100筆容量，請使用預設記憶體保留或等待7日期限清理。', ASSET_REVIEW_FLOOR_REQUIRED: '設備業務複核必須保留雙人、複核者角色、證據參照及至少16字理由。', QUALITY_V2_FLOOR_REQUIRED: 'v5候選不得降低既有品質要求：Schema／拒答100%、任務維修80%／設備85%、生成P95最多5000ms。',
  LOAD_RESOURCE_BUDGET_INSUFFICIENT: '可用 RAM 未達負載開始底線，請稍後重測資源。', LOAD_ALREADY_RUNNING: '已有負載執行，請等待或安全停止。', LOAD_TOKEN_BUDGET_EXCEEDED: '負載 Token 上限最多 384，請保存受限設定。', BACKUP_FORMAT_HASH_OR_COMPATIBILITY_INVALID: '備份格式、完整性或相容性不符；未還原資料。', BACKUP_UPLOAD_EXPIRED: '備份上傳已過期，請重新選檔。', MLFLOW_REQUIRES_REAL_REPORT: 'MLflow 只同步完成的真實 Q8 報告。', REPORT_HASH_MISMATCH: '報告完整性不符，已拒絕同步。',
  SAFETY_FLOOR_REQUIRED: '設定缺少不可降低的安全底線。', ASSET_POLICY_REQUIRED: '設備版必須保留中風險／關鍵設備複核、雙核准及維護時段。',
  RELEASE_GATE_FAILED: '評測或版本證據未通過發布閘門，請查看檢查原因。', SELF_APPROVAL_FORBIDDEN: '不能核准自己提出的發布。',
  DUPLICATE_APPROVAL: '同一人不能重複核准。', APPROVALS_REQUIRED: '尚未完成獨立複核者核准。',
  ACTIVE_VERSION_CHANGED: '線上版本已改變，請更新狀態後重新確認。', RELEASE_NOT_READY: '發布服務未就緒，請由管理者驗證並恢復目前版本。',
  RELEASE_CONTRACT_CHANGED: '程式或產物契約已改變，需重新評測。', MAINTENANCE_WINDOW_REQUIRED: '設備版發布與回滾需要确认維護時段。',
  RELEASE_PROBE_FAILED: '真實推論驗證失敗，未套用目標版本。請查看恢復證據。', CONFIG_NOT_FOUND: '設定版本不存在。',
  DATASET_HASH_MISMATCH: '固定評測集內容雜湊不符，已阻擋執行。', ACTIVE_VERSION_CONFLICT: '指定設定與線上版本不符，請使用線上模式或明示沙盒。',
  AUTH_REQUIRED: '登入已失效，請重新登入。', INVALID_CREDENTIALS: '帳號或密碼不正確。',
  INITIALIZED: '管理者已建立，請使用登入入口。', USERNAME_EXISTS: '帳號已存在，請使用其他名稱。',
  ROLE_FORBIDDEN: '此操作需要管理者權限。', CSRF_INVALID: '操作憑證已失效，請重新登入。',
  ORIGIN_FORBIDDEN: '操作來源不符，請從本機控制台重試。', LOGIN_RATE_LIMITED: '登入嘗試過於頻繁，請稍後再試。',
  ENGINE_UNAVAILABLE: '真實模型引擎尚未就緒。請由管理者啟動，或主動選擇 Fixture 測試。',
  ENGINE_BUSY: '引擎或佇列仍在工作，請等待完成再操作。', ENGINE_CONTROL_IN_PROGRESS: '引擎正在啟動或驗證，請等待完成。',
  ENGINE_START_FAILED: '引擎啟動失敗。請檢查作品內的私有引擎紀錄與資源，再重試。',
  ENGINE_PORT_OCCUPIED: '引擎連接埠已被占用，未接管其他服務。請檢查後再啟動。',
  QUEUE_FULL: '有限佇列已滿，請稍後重新提交。', QUEUE_TIMEOUT: '等待超過設定期限，此工作未執行。',
  MODEL_TIMEOUT: '模型生成逾時。系統仍會等候工作結束或停止引擎，期間保留容量槽。',
  MODEL_OUTPUT_INVALID: '模型輸出未通過 Schema 驗證，未產生正式結果。',
  MODEL_OUTPUT_TRUNCATED: '模型輸出被截斷，未產生正式結果。',
  MODEL_INFERENCE_ERROR: '模型請求失敗，請檢查健康狀態後重試。',
  RUNNING_NOT_CANCELLABLE: '此階段尚無安全取消能力。工作會保留容量直到真正結束。',
  TASK_NOT_CANCELLABLE: '只有尚未執行的推論工作可以取消。', SERVICE_RESTARTED: '服务重啟中斷了工作，未自動重播。請重新提交。',
  VALIDATION_ERROR: '欄位格式不符，請依表單限制修正。', PROFILE_FIELDS_REQUIRED: '此設定需要補齊設備重要性及維護時段。',
  BODY_TOO_LARGE: '輸入超過大小上限。', INTERNAL_ERROR: '操作失敗，請保留追蹤編號並重試。',
  ARTIFACT_HASH_MISMATCH: '模型或引擎檔案雜湊不符，已阻擋啟動。',
  ARTIFACT_MISSING_OR_INVALID: '模型、引擎或來源契約缺失，請依安裝說明準備。',
  LICENSE_MISMATCH: '模型授權檔與匯入契約不符，已阻擋啟動。',
};
export function errorMessage(code: string) { return messages[code] || `操作未完成（${code}），請查看健康狀態或重試。`; }
export class ApiError extends Error {
  constructor(public code: string, public requestId?: string, public fields?: unknown) { super(errorMessage(code)); }
}
export async function api<T>(path: string, method = 'GET', body?: unknown, headers?: Record<string, string>): Promise<T> {
  let response: Response;
  try {
    response = await fetch('/api' + path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Console-Request': '1', ...(csrf ? { 'X-CSRF-Token': csrf } : {}), ...headers }, ...(method !== 'GET' ? { body: JSON.stringify(body ?? {}) } : {}) });
  } catch { throw new ApiError('NETWORK_UNAVAILABLE'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error?.code || 'INTERNAL_ERROR', data.error?.request_id, data.error?.fields);
  return data as T;
}
export const terminal = (status: string) => ['completed', 'needs_review', 'failed', 'cancelled'].includes(status);
export const date = (t: number) => new Date(t * 1000).toLocaleString('zh-TW', { hour12: false });
export const statusText: Record<string, string> = { queued: '等待中', running: '執行中', validating: '安全驗證', completed: '已完成', needs_review: '需要複核', failed: '失敗', cancelled: '已取消', stopped: '未啟動', ready: '就緒', loading: '載入中', verifying: '核對中', unhealthy: '不健康' };
export const roleText = { admin: '管理者', reviewer: '複核者', operator: '一般使用者' };
export const eventText: Record<string, string> = { mechanical: '機械', electrical: '電氣', thermal: '溫度', pressure: '壓力', communication: '通訊', process: '製程', unknown: '資訊不足' };
export const severityText: Record<string, string> = { low: '低', medium: '中', high: '高', critical: '危急', unknown: '未知' };
export const actionText: Record<string, string> = { observe: '觀察與追蹤', collect_evidence: '補充證據', isolate_and_escalate: '隔離並通知負責人', stop_and_escalate: '停止使用並通知負責人' };
