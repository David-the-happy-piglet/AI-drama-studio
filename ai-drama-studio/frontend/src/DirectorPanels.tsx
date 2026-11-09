import { useEffect, useState } from "react";
import { ClipboardCheck, ListChecks, Palette, ScanEye, Wand2 } from "lucide-react";
import { api, errorMessage, postJson } from "./api";
import { Callout, Field, Modal } from "./components";
import type { CompiledPrompt, DirectorStatus, LintFinding, LintResult, Project, Review, ReviewGate, ReviewReport, Shot, ShotDirection } from "./types";

type Notify = (message: string, kind?: "success" | "error") => void;

const severityLabel: Record<string, string> = { error: "错误", warning: "注意", info: "提示" };
const stageLabel: Record<string, string> = { story: "剧本冷读", storyboard: "分镜冷读", shot_pre_submit: "提交前审核" };

let statusCache: Promise<DirectorStatus> | null = null;
export function useDirectorStatus() {
  const [status, setStatus] = useState<DirectorStatus>();
  useEffect(() => {
    statusCache ??= api<DirectorStatus>("/director/status");
    statusCache.then(setStatus).catch(() => { statusCache = null; });
  }, []);
  return status;
}

export function LintList({ result }: { result: LintResult }) {
  const { summary, findings } = result;
  const [showInfo, setShowInfo] = useState(false);
  if (!findings.length) return <Callout tone="success">规则检查没有发现问题。规则是关键词启发式，仍需人工看一遍。</Callout>;
  const visible = findings.filter(item => showInfo || item.severity !== "info");
  const groups: Array<[string, LintFinding[]]> = [];
  for (const item of visible) {
    const last = groups[groups.length - 1];
    if (last && last[0] === item.ref) last[1].push(item); else groups.push([item.ref, [item]]);
  }
  // Collapse per-shot numbers so "台词 28 字…" and "台词 15 字…" count as one kind.
  const kind = (message: string) => message.replace(/（.*）$/, "").replace(/\d+(\.\d+)?/g, "N");
  const byRule: Record<string, number> = {};
  for (const item of findings) byRule[kind(item.message)] = (byRule[kind(item.message)] || 0) + 1;
  const top = Object.entries(byRule).sort((a, b) => b[1] - a[1]).slice(0, 4);
  return <div className="director-findings">
    <p className="muted">注意 {summary.warning || 0} · 提示 {summary.info || 0}，涉及 {new Set(findings.map(item => item.ref)).size} 个镜头。规则是关键词启发式，命中代表“再看一眼”，不是定论。</p>
    <p className="muted">最常见：{top.map(([message, count]) => `${message}（${count}）`).join("；")}</p>
    <label className="quota-confirm"><input type="checkbox" checked={showInfo} onChange={e => setShowInfo(e.target.checked)} />同时显示“提示”级别</label>
    <ul>{groups.map(([ref, items]) => <li key={ref} className={`finding ${items.some(item => item.severity !== "info") ? "warning" : "info"}`}>
      <span className="finding-tag">{ref}</span>
      <div>{items.map((item, index) => <div key={`${item.rule}-${index}`}>{item.severity === "info" ? "· " : "▲ "}{item.message}{item.suggestion && <small>建议：{item.suggestion}</small>}</div>)}</div>
    </li>)}</ul>
  </div>;
}

export function ReviewReportView({ report, meta }: { report: ReviewReport; meta?: string }) {
  return <div className={`review-report ${report.verdict}`}>
    <div className="review-verdict"><strong>{report.verdict === "pass" ? "通过" : "不通过"}</strong>{meta && <span>{meta}</span>}</div>
    {report.understood_story && <p><b>审核看懂的内容：</b>{report.understood_story}</p>}
    {report.one_line_theme && <p><b>一句话主题：</b>{report.one_line_theme}</p>}
    {report.issues.length > 0 && <ul>{report.issues.map((issue, index) => <li key={index} className={issue.severity === "硬伤" ? "finding warning" : "finding info"}>
      <span className="finding-tag">{issue.severity}{issue.certainty === "可能" ? " · 可能" : ""}</span>
      <div>{issue.target && <strong>{issue.target}</strong>} {issue.problem}{issue.suggestion && <small>建议：{issue.suggestion}</small>}</div>
    </li>)}</ul>}
    {report.cannot_check.length > 0 && <p className="muted">查不了：{report.cannot_check.join("；")}</p>}
  </div>;
}

function reviewMeta(review: Review) {
  return `${stageLabel[review.stage]} · 修订 ${review.target_version} · ${review.model} · ${new Date(review.created_at).toLocaleString()}`;
}

/** Fresh-context review of the saved screenplay or storyboard. */
export function ColdReviewPanel({ scriptId, scriptVersion, stage, dirty, notify }: { scriptId: string; scriptVersion: number; stage: "story" | "storyboard"; dirty: boolean; notify: Notify }) {
  const status = useDirectorStatus();
  const [reviews, setReviews] = useState<Review[]>([]);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  useEffect(() => {
    let active = true;
    setReviews([]);
    void api<Review[]>(`/reviews?target_id=${encodeURIComponent(scriptId)}`)
      .then(items => { if (active) setReviews(items.filter(item => item.stage === stage)); })
      .catch(() => {});
    return () => { active = false; };
  }, [scriptId, stage]);
  async function run() {
    setBusy(true);
    try {
      const review = await postJson<Review>(`/scripts/${scriptId}/reviews`, { stage });
      setReviews(current => [review, ...current]); setConfirming(false);
      notify(review.verdict === "pass" ? "冷读审核通过" : "冷读审核不通过，请看审核意见", review.verdict === "pass" ? "success" : "error");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  const latest = reviews[0];
  const stale = latest && latest.target_version !== scriptVersion;
  return <div className="director-box">
    <div className="director-box-heading"><ScanEye size={16} /><strong>{stageLabel[stage]}</strong><span>新开一个不知道背景的审核，只给它{stage === "story" ? "剧本正文" : "分镜表"}，只挑错不改稿。</span></div>
    {!status?.review_model.configured && <Callout>审核模型未配置：设置 REVIEW_API_URL / REVIEW_API_KEY / REVIEW_MODEL，或沿用 PLANNING_* 配置。</Callout>}
    <div className="page-actions"><button type="button" className="soft-button" disabled={busy || dirty || !status?.review_model.configured} onClick={() => setConfirming(true)}><ScanEye size={14} /> 运行{stageLabel[stage]}</button>
      {dirty && <small className="muted">有未保存修改，审核的是已保存版本，请先保存。</small>}</div>
    {latest && <>{stale && <Callout tone="warning">这份审核针对修订 {latest.target_version}，当前已是修订 {scriptVersion}，建议重新审核。</Callout>}<ReviewReportView report={latest.report} meta={reviewMeta(latest)} /></>}
    {reviews.length > 1 && <details><summary className="muted">更早的 {reviews.length - 1} 次审核</summary>{reviews.slice(1).map(item => <ReviewReportView key={item.id} report={item.report} meta={reviewMeta(item)} />)}</details>}
    {confirming && <Modal title={`运行${stageLabel[stage]}`} onClose={() => !busy && setConfirming(false)}>
      <Callout tone="warning">将把已保存的{stage === "story" ? "剧本正文" : "分镜表"}发送给审核模型 {status?.review_model.model}，可能产生费用。审核只给意见，不会修改剧本或分镜。</Callout>
      <div className="modal-actions"><button className="ghost-button" disabled={busy} onClick={() => setConfirming(false)}>返回</button><button className="primary-button" disabled={busy} onClick={() => void run()}>{busy ? "审核中…" : "确认审核"}</button></div>
    </Modal>}
  </div>;
}

/** Free, local rule check of the unsaved draft. */
export function DraftLintPanel({ scenes, notify }: { scenes: Array<{ shots: Array<Partial<Shot>> }>; notify: Notify }) {
  const [result, setResult] = useState<LintResult | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try { setResult(await postJson<LintResult>("/storyboard-lint", { scenes })); }
    catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  return <div className="director-box">
    <div className="director-box-heading"><ListChecks size={16} /><strong>分镜规则检查</strong><span>不调用模型，不花钱：角度、景别、什么在动、AI 易错画面、台词时长。</span></div>
    <div className="page-actions"><button type="button" className="soft-button" disabled={busy} onClick={() => void run()}><ListChecks size={14} /> {result ? "重新检查" : "检查当前分镜"}</button></div>
    {result && <LintList result={result} />}
  </div>;
}

/** Director intent per shot; empty values are omitted. */
export function DirectionFields({ value, onChange }: { value?: ShotDirection; onChange: (next: ShotDirection) => void }) {
  const direction = value || {};
  const set = (patch: ShotDirection) => onChange({ ...direction, ...patch });
  return <details className="direction-fields" open={Boolean(direction.motion_source?.length || direction.first_frame)}>
    <summary>导演设计（什么在动、走向、首尾帧）</summary>
    <div className="field-grid two">
      <Field label="这个镜头什么在动" hint="用逗号分隔：摄影机推近、人物走近、前景飘过的纸…"><input value={(direction.motion_source || []).join("，")} onChange={e => set({ motion_source: e.target.value.split(/[,，、]/).map(item => item.trim()).filter(Boolean) })} /></Field>
      <Field label="谁往哪走（画面左右）"><input value={direction.screen_direction || ""} onChange={e => set({ screen_direction: e.target.value })} placeholder="林夏从画面右侧走向左侧门口" /></Field>
      <Field label="进画 / 出画"><input value={direction.frame_entry_exit || ""} onChange={e => set({ frame_entry_exit: e.target.value })} /></Field>
      <Field label="靠什么表情推进"><input value={direction.expression_beat || ""} onChange={e => set({ expression_beat: e.target.value })} /></Field>
      <Field label="首帧画面" hint="动作开始那一刻；动作要用的道具、旁人的位置都要在首帧里。"><textarea rows={2} value={direction.first_frame || ""} onChange={e => set({ first_frame: e.target.value })} /></Field>
      <Field label="尾帧画面"><textarea rows={2} value={direction.last_frame || ""} onChange={e => set({ last_frame: e.target.value })} /></Field>
    </div>
    <Field label="怎么转到下一镜"><input value={direction.transition_out || ""} onChange={e => set({ transition_out: e.target.value })} /></Field>
  </details>;
}

/** Visual style + palette feed the seven-section prompt; lint the published episode. */
export function DirectorBoardPanel({ project, episodeId, notify }: { project: Project; episodeId: string; notify: Notify }) {
  const [style, setStyle] = useState(String(project.settings_json.visual_style || ""));
  const [palette, setPalette] = useState(String(project.settings_json.palette || ""));
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<LintResult | null>(null);
  const [linting, setLinting] = useState(false);
  useEffect(() => { setStyle(String(project.settings_json.visual_style || "")); setPalette(String(project.settings_json.palette || "")); }, [project.id]);
  useEffect(() => { setResult(null); }, [episodeId]);
  async function save() {
    setSaving(true);
    try {
      const settings = await api<Record<string, unknown>>(`/projects/${project.id}/director-settings`, { method: "PUT", body: JSON.stringify({ visual_style: style, palette }) });
      Object.assign(project.settings_json, settings);
      notify("画风与配色已保存，之后生成的七段式提示词会使用它们");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setSaving(false); }
  }
  async function lint() {
    setLinting(true);
    try { setResult(await api<LintResult>(`/episodes/${episodeId}/storyboard-lint`)); }
    catch (error) { notify(errorMessage(error), "error"); } finally { setLinting(false); }
  }
  return <section className="panel director-board">
    <div className="panel-heading"><div><span className="eyebrow">DIRECTOR</span><h3>画风、配色与分镜检查</h3></div><Palette size={18} /></div>
    <div className="field-grid two">
      <Field label="画风（全片统一）" hint="一句话写死，例如：电影写实，自然光，浅景深，无颗粒"><textarea rows={2} value={style} onChange={e => setStyle(e.target.value)} /></Field>
      <Field label="配色 / 色表" hint="主色、强调色、每场用什么颜色；画第一张图前定好"><textarea rows={2} value={palette} onChange={e => setPalette(e.target.value)} /></Field>
    </div>
    <div className="page-actions"><button type="button" className="soft-button" disabled={saving} onClick={() => void save()}><Palette size={14} /> 保存画风与配色</button>
      {episodeId && <button type="button" className="soft-button" disabled={linting} onClick={() => void lint()}><ListChecks size={14} /> 检查本集分镜规则</button>}</div>
    {result && <LintList result={result} />}
  </section>;
}

/** Seven-section compile + pre-submit cold review for the generation page. */
export function PromptDirectorTools({ shot, prompt, frameSource, onUsePrompt, onShotUpdated, onGate, notify, disabled }: {
  shot: Shot;
  prompt: string;
  frameSource: "selected" | "upload" | "none";
  onUsePrompt: (prompt: string) => void;
  onShotUpdated: (shot: Shot) => void;
  onGate: (gate: ReviewGate | null) => void;
  notify: Notify;
  disabled?: boolean;
}) {
  const status = useDirectorStatus();
  const [compiled, setCompiled] = useState<CompiledPrompt | null>(null);
  const [gate, setGate] = useState<ReviewGate | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmReview, setConfirmReview] = useState(false);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      if (!prompt.trim()) { setGate(null); onGate(null); return; }
      void postJson<ReviewGate>(`/shots/${shot.id}/review-gate`, { prompt, first_frame: frameSource })
        .then(next => { if (active) { setGate(next); onGate(next); } })
        .catch(() => { if (active) { setGate(null); onGate(null); } });
    }, 400);
    return () => { active = false; window.clearTimeout(timer); };
  }, [shot.id, shot.version, prompt, frameSource]);

  async function compile() {
    setBusy(true);
    try { setCompiled(await api<CompiledPrompt>(`/shots/${shot.id}/compiled-prompt`)); }
    catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function saveToShot() {
    setBusy(true);
    try {
      const updated = await postJson<Shot>(`/shots/${shot.id}/compiled-prompt/apply`, { expected_version: shot.version });
      onShotUpdated(updated); onUsePrompt(updated.generation_prompt || ""); setCompiled(null);
      notify("七段式提示词已保存到镜头（新修订），并填入本次提示词");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function review() {
    setBusy(true);
    try {
      const result = await postJson<Review>(`/shots/${shot.id}/reviews`, { prompt });
      const next: ReviewGate = { mode: gate?.mode || status?.review_gate || "warn", verdict: result.verdict, review_id: result.id, report: result.report };
      setGate(next); onGate(next); setConfirmReview(false);
      notify(result.verdict === "pass" ? "提交前审核通过" : "提交前审核不通过，请看审核意见", result.verdict === "pass" ? "success" : "error");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }

  const verdictText = gate?.verdict === "pass" ? "已通过" : gate?.verdict === "fail" ? "未通过" : "未审核";
  return <div className="director-box">
    <div className="director-box-heading"><ClipboardCheck size={16} /><strong>提示词与提交前审核</strong>
      <span className={`gate-badge ${gate?.verdict || "missing"}`}>{verdictText}{gate?.mode === "required" ? " · 必须通过" : gate?.mode === "warn" ? " · 仅记录" : ""}</span></div>
    <div className="page-actions">
      <button type="button" className="soft-button" disabled={busy || disabled} onClick={() => void compile()}><Wand2 size={14} /> 生成七段式提示词</button>
      <button type="button" className="soft-button" disabled={busy || disabled || !prompt.trim() || !status?.review_model.configured} onClick={() => setConfirmReview(true)}><ScanEye size={14} /> 提交前审核</button>
    </div>
    {!status?.review_model.configured && <small className="muted">审核模型未配置（REVIEW_* 或 PLANNING_*），无法运行提交前审核。</small>}
    <small className="muted">审核针对“当前这段提示词 + {frameSource === "selected" ? "已选首帧" : frameSource === "upload" ? "手动上传的首帧（无法预先审核）" : "无首帧"}”。改了提示词、镜头或首帧，需要重新审核。</small>
    {gate?.report && <ReviewReportView report={gate.report} />}
    {compiled && <Modal title="七段式提示词预览" eyebrow="SEVEN-SECTION PROMPT" wide onClose={() => !busy && setCompiled(null)}>
      {compiled.notes.length > 0 && <Callout>{compiled.notes.join("；")}</Callout>}
      <div className="compiled-sections">{Object.entries(compiled.sections).map(([name, text]) => <div key={name}><strong>【{name}】</strong><p>{text}</p></div>)}</div>
      <div className="modal-actions">
        <button className="ghost-button" disabled={busy} onClick={() => setCompiled(null)}>关闭</button>
        <button className="soft-button" disabled={busy} onClick={() => { onUsePrompt(compiled.prompt); setCompiled(null); notify("已填入本次提示词，未保存到镜头"); }}>仅填入本次提示词</button>
        <button className="primary-button" disabled={busy || disabled} onClick={() => void saveToShot()}>保存到镜头并填入</button>
      </div>
    </Modal>}
    {confirmReview && <Modal title="提交前审核" onClose={() => !busy && setConfirmReview(false)}>
      <Callout tone="warning">将把当前提示词、分镜行、人物设定、前后镜头{frameSource === "selected" ? "、已选首帧" : ""}和角色母版图发送给审核模型 {status?.review_model.model}，可能产生费用。审核只给意见，不修改镜头，也不提交视频。</Callout>
      <div className="modal-actions"><button className="ghost-button" disabled={busy} onClick={() => setConfirmReview(false)}>返回</button><button className="primary-button" disabled={busy} onClick={() => void review()}>{busy ? "审核中…" : "确认审核"}</button></div>
    </Modal>}
  </div>;
}
