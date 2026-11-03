import { useEffect, useRef, useState } from "react";
import { ImagePlus, RefreshCw, Sparkles } from "lucide-react";
import { api, ApiError, errorMessage, mediaUrl, postJson } from "./api";
import { Callout, Field, Modal } from "./components";
import { mergePortraitJob, portraitNeedsPolling, portraitPending, portraitStatus, portraitInputLabel, referenceEffectivePrompt, referencePresets } from "./portraitJobs";
import type { PortraitJob, PortraitStudio } from "./portraitJobs";
import type { Character } from "./types";

type Submission = { expected_version: number; request_token: string; prompt: string;
  negative_prompt: string; count: number; resolution: string; aspect_ratio: string; confirmed: true;
  mode: "text" | "reference"; reference_path?: string; reference_sha256?: string; reference_confirmed?: boolean };

export function CharacterPortraitModal({ character: initial, onClose, onSaved, onReferences, notify }: {
  character: Character; onClose: () => void; onSaved: () => void; onReferences: (character: Character) => void;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [character, setCharacter] = useState(initial);
  const [studio, setStudio] = useState<PortraitStudio | null>(null);
  const [jobs, setJobs] = useState<PortraitJob[]>([]);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [mode, setMode] = useState<"text" | "reference">(initial.master_reference_path ? "reference" : "text");
  const [count, setCount] = useState(initial.master_reference_path ? 1 : 4);
  const [resolution, setResolution] = useState("1k");
  const [ratio, setRatio] = useState("3:4");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingRequest, setPendingRequest] = useState<Submission | null>(null);
  const [selected, setSelected] = useState<{ jobId: string; index: number } | null>(null);
  const [confirmSelection, setConfirmSelection] = useState(false);
  const initialized = useRef(false);
  const busyRef = useRef(false);
  const chosenJob = jobs.find(item => item.id === selected?.jobId);
  const chosen = chosenJob?.candidates.find(item => item.index === selected?.index);
  const active = jobs.some(portraitPending);
  const locked = busy || Boolean(pendingRequest);
  const pollKey = jobs.filter(portraitNeedsPolling).map(item => item.id).sort().join(",");
  const base = `/characters/${character.id}`;
  const referenceReady = Boolean(studio?.reference.path && studio.reference.sha256 && !studio.reference.error);
  const actualPrompt = mode === "reference" ? referenceEffectivePrompt(studio?.reference_prefix || "", prompt, negative) : prompt.trim();
  const canSubmit = Boolean(studio?.configured && prompt.trim() && actualPrompt.length <= 2500 && (mode === "text" || referenceReady));

  function changeMode(next: "text" | "reference") {
    setMode(next); setConfirmed(false);
    setPrompt(next === "reference" ? studio?.reference_prompt || referencePresets.front : studio?.prompt || "");
    setCount(next === "reference" ? 1 : 4);
  }

  useEffect(() => {
    let cancelled = false;
    api<PortraitStudio>(`${base}/portrait-studio`).then(data => {
      if (cancelled) return;
      setStudio(data); setJobs(data.jobs);
      if (!initialized.current) { setPrompt(mode === "reference" ? data.reference_prompt : data.prompt); setNegative(data.negative_prompt); initialized.current = true; }
    }).catch(err => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [base]);

  useEffect(() => {
    if (!pollKey) return;
    let cancelled = false, polls = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!busyRef.current) {
        for (const id of pollKey.split(",")) {
          if (cancelled) return;
          try {
            const next = await postJson<PortraitJob>(`${base}/portrait-jobs/${id}/refresh`, {});
            if (!cancelled) setJobs(items => mergePortraitJob(items, next));
          } catch (err) { if (!cancelled) setError(errorMessage(err)); }
        }
      }
      if (!cancelled && ++polls < 24) timer = setTimeout(() => void poll(), 10000);
    }
    timer = setTimeout(() => void poll(), 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [base, pollKey]);

  function begin() { busyRef.current = true; setBusy(true); setError(""); }
  function end() { busyRef.current = false; setBusy(false); }
  async function reload() {
    begin();
    try {
      const [data, fresh] = await Promise.all([api<PortraitStudio>(`${base}/portrait-studio`), api<Character>(base)]);
      setStudio(data); setJobs(data.jobs); setCharacter(fresh);
      setConfirmed(false);
      if (!initialized.current) { setPrompt(mode === "reference" ? data.reference_prompt : data.prompt); setNegative(data.negative_prompt); initialized.current = true; }
      if (pendingRequest && data.jobs.some(item => item.request_token === pendingRequest.request_token)) setPendingRequest(null);
    } catch (err) { setError(errorMessage(err)); }
    finally { end(); }
  }
  async function submit(recover = false) {
    if (busyRef.current) return;
    if (!recover && (!confirmed || active || !canSubmit)) return;
    const body = recover ? pendingRequest : { expected_version: character.version,
      request_token: crypto.randomUUID(), prompt, negative_prompt: negative,
      count, resolution, aspect_ratio: ratio, confirmed: true as const, mode,
      ...(mode === "reference" ? { reference_path: studio!.reference.path!, reference_sha256: studio!.reference.sha256!, reference_confirmed: true } : {}) };
    if (!body) return;
    begin(); setPendingRequest(body);
    try {
      const job = await postJson<PortraitJob>(`${base}/portrait-jobs`, body);
      setJobs(items => mergePortraitJob(items, job)); setPendingRequest(null); setConfirmed(false);
      notify(job.error_message || "候选图任务已提交；选定前不会改变角色母版", job.error_message ? "error" : "success");
    } catch (err) {
      if (err instanceof ApiError && err.status < 500 && err.status !== 408) setPendingRequest(null);
      setError(errorMessage(err));
    } finally { end(); }
  }
  async function refresh(job: PortraitJob) {
    begin();
    try { const next = await postJson<PortraitJob>(`${base}/portrait-jobs/${job.id}/refresh`, {}); setJobs(items => mergePortraitJob(items, next)); }
    catch (err) { setError(errorMessage(err)); }
    finally { end(); }
  }
  async function adopt() {
    if (!chosenJob || !chosen?.path || busyRef.current) return;
    if (!confirmSelection) { setConfirmSelection(true); return; }
    const stale = chosenJob.source_version !== character.version;
    begin();
    try {
      const saved = await postJson<Character>(`${base}/portrait-jobs/${chosenJob.id}/select`, {
        expected_version: character.version, candidate_index: chosen.index, confirmed: true, accept_stale: stale,
      });
      setCharacter(saved); setConfirmed(false); setConfirmSelection(false); onSaved(); notify("已设为本地母版，尚未上传可灵");
      setStudio(await api<PortraitStudio>(`${base}/portrait-studio`));
    } catch (err) { setError(errorMessage(err)); }
    finally { end(); }
  }

  return <Modal title={`${character.name} · 角色参考图生成`} eyebrow={mode === "reference" ? "REFERENCE IMAGE / KLING IMAGE 3.0 OMNI" : "TEXT ONLY / KLING IMAGE 3.0"} wide onClose={() => !busy && onClose()}>
    <div className="portrait-studio">
      <section className="modal-form">
        <Field label="生成模式" hint="切换模式会恢复该模式的默认提示词与张数。">
          <select value={mode} disabled={locked || !studio} onChange={e => changeMode(e.target.value as "text" | "reference")}><option value="reference">基于母版参考图生成 · Image 3.0 Omni</option><option value="text">纯文字生成新角色 · 不参考图片</option></select>
        </Field>
        {mode === "reference" ? <>
          <Callout>实际上传下方母版图片，以它作为人物参考。一次生成的多张图使用同一角度要求，并非自动多角度套图；仍需检查五官是否一致。</Callout>
          {studio?.reference.path && <div className="current-portrait"><img src={mediaUrl(studio.reference.path) || ""} alt="本次将上传给可灵的母版参考图" /><div><strong>本次将上传的母版图</strong><p>只发送这一张，不发送其他角色或补充图。</p><small>图片指纹：{studio.reference.sha256?.slice(0, 12) || "未通过校验"}</small></div></div>}
          {!referenceReady && <Callout tone="warning">{studio?.reference.error || "请先上传并保存一张 JPG/PNG 母版图。"}<button className="text-button" disabled={locked} onClick={() => onReferences(character)}>管理母版参考图</button></Callout>}
          <Field label="角度预设" hint="选择后替换下方生成要求，可继续手动修改。"><select disabled={locked} value="custom" onChange={e => { if (referencePresets[e.target.value]) { setPrompt(referencePresets[e.target.value]); setConfirmed(false); } }}><option value="custom">选择预设 / 自定义要求</option><option value="front">正面头肩照</option><option value="three_quarter">45° 半侧面</option><option value="profile">90° 侧面</option><option value="full_body">正面全身照</option></select></Field>
        </> : <Callout tone="warning">本模式只发送文字，绝不使用已上传的母版图片。适合从零挑选新角色长相；若要沿用照片里的人，请切换到“基于母版参考图生成”。</Callout>}
        <Field label={mode === "reference" ? "生成要求" : "母版 prompt"} hint={mode === "reference" ? "描述想改变的角度、服装或表情，不必重写五官。系统会加入图片身份引用；不会修改原剧本。" : studio ? `来源：${studio.prompt_source}。编辑仅用于本次生成，不改原剧本。` : "读取已有母版 prompt…"}>
          <textarea rows={7} maxLength={2500} value={prompt} disabled={locked || !studio} onChange={e => { setPrompt(e.target.value); setConfirmed(false); }} />
          <small>{actualPrompt.length}/2500 字{mode === "reference" && "（含图片身份约束及负向约束）"}</small>
        </Field>
        <Field label="负向约束"><textarea rows={2} maxLength={2500} value={negative} disabled={locked} onChange={e => { setNegative(e.target.value); setConfirmed(false); }} /></Field>
        {mode === "reference" && <details><summary>查看实际发送的完整提示词</summary><p className="portrait-prompt">{actualPrompt}</p></details>}
        <div className="field-grid three">
          <Field label="候选张数"><select disabled={locked} value={count} onChange={e => { setCount(Number(e.target.value)); setConfirmed(false); }}>{[1, 2, 3, 4].map(n => <option value={n} key={n}>{n} 张</option>)}</select></Field>
          <Field label="清晰度"><select disabled={locked} value={resolution} onChange={e => { setResolution(e.target.value); setConfirmed(false); }}><option value="1k">1K</option><option value="2k">2K</option></select></Field>
          <Field label="画幅"><select disabled={locked} value={ratio} onChange={e => { setRatio(e.target.value); setConfirmed(false); }}><option>3:4</option><option>1:1</option><option>2:3</option></select></Field>
        </div>
        {studio && !studio.configured && <Callout tone="warning">{studio.configuration_issues.join("；")}。配置就绪不等于已验证图片模型权限或额度。</Callout>}
        {active && <Callout>已有待完成或待核实的任务。请在候选图历史里刷新，不会自动重新提交。</Callout>}
        <label className="quota-confirm"><input type="checkbox" disabled={locked || active || !canSubmit} checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />{mode === "reference" ? "我有权使用此图片，并确认将上方母版图片与完整提示词发送给可灵 Image 3.0 Omni" : "确认仅发送文字给可灵 Image 3.0，不使用任何母版图片"}，生成 {count} 张 {resolution.toUpperCase()} 候选图；费用以可灵 API 账单为准。不自动替换母版、不创建可灵主体或视频。</label>
        <button className="primary-button" disabled={locked || active || !confirmed || !canSubmit} onClick={() => void submit()}><Sparkles size={16} />{busy ? "处理中…" : mode === "reference" ? `基于母版生成 ${count} 张候选图` : `纯文字生成 ${count} 张新角色图`}</button>
        {pendingRequest && <Callout tone="warning">本次提交尚未确认。可先“刷新列表”；若未找回，点击“恢复本次提交”会复用相同提交标识，防止重复扣费。<button className="soft-button" disabled={busy} onClick={() => void submit(true)}>恢复本次提交</button></Callout>}
        {error && <Callout tone="warning">{error}</Callout>}
      </section>
      <section className="modal-form">
        <div className="panel-heading"><h3>候选图与生成历史</h3><button className="soft-button" disabled={busy} onClick={() => void reload()}><RefreshCw size={14} />刷新列表</button></div>
        {character.master_reference_path && <div className="current-portrait"><img src={mediaUrl(character.master_reference_path) || ""} alt="当前母版" /><div><strong>当前母版</strong><p>不会被新生成的候选图自动替换。</p><button className="text-button" disabled={locked} onClick={() => onReferences(character)}>补充参考图 / 创建可灵主体</button></div></div>}
        {!jobs.length && <p className="muted">尚无候选图。确认左侧 prompt 后生成；生成结果会保留，关闭窗口后可以继续查看。</p>}
        {chosen?.path && chosenJob && <div className="portrait-selection">
          <img src={mediaUrl(chosen.path) || ""} alt={`${character.name} 选中候选 ${chosen.index + 1}`} />
          {confirmSelection && <Callout tone="warning">{chosenJob.source_version !== character.version && "此候选图来自较早角色版本。"}确认采用候选 {chosen.index + 1} 作为 {character.name} 的母版？现有母版将被替换，旧补充图引用会清空（文件保留），已有可灵主体绑定将过期。不会自动上传可灵或生成视频。<button className="text-button" disabled={busy} onClick={() => setConfirmSelection(false)}>取消替换</button></Callout>}
          <button className="primary-button" disabled={locked || chosen.path === character.master_reference_path} onClick={() => void adopt()}><ImagePlus size={16} />{chosen.path === character.master_reference_path ? "已是当前母版" : confirmSelection ? "确认设为母版" : "将选中图片设为母版"}</button>
        </div>}
        {jobs.map(job => <article className="portrait-batch" key={job.id}>
          <strong>{portraitInputLabel(job)}</strong>
          <div className="panel-heading"><div><strong>{portraitStatus[job.status] || job.status}</strong><small>{new Date(job.created_at).toLocaleString()} · {job.count} 张 · {job.resolution.toUpperCase()} · {job.aspect_ratio}</small></div><button className="text-button" disabled={busy} onClick={() => void refresh(job)}>{["partial", "download_failed"].includes(job.status) ? "重试下载" : "刷新状态"}</button></div>
          {job.error_message && <p className="portrait-error">{job.error_message}</p>}
          <div className="portrait-candidates">{job.candidates.map(candidate => <div key={candidate.index}>
            {candidate.path ? <button className={selected?.jobId === job.id && selected.index === candidate.index ? "portrait-option selected" : "portrait-option"} aria-pressed={selected?.jobId === job.id && selected.index === candidate.index} onClick={() => { setSelected({ jobId: job.id, index: candidate.index }); setConfirmSelection(false); }}>
              <img src={mediaUrl(candidate.path) || ""} alt={`${character.name} 候选 ${candidate.index + 1}`} loading="lazy" /><span>{candidate.path === character.master_reference_path ? "当前母版" : `选择候选 ${candidate.index + 1}`}</span>
            </button> : <div className="portrait-placeholder">候选 {candidate.index + 1}<small>{candidate.error || "等待保存图片"}</small></div>}
          </div>)}</div>
          <details><summary>本次输入图片、prompt 与任务信息</summary>{job.reference_snapshot_path && <div className="current-portrait"><img src={mediaUrl(job.reference_snapshot_path) || ""} alt="该任务的输入母版快照" /><div><strong>该任务的输入母版快照</strong><small>SHA-256：{job.reference_sha256}</small></div></div>}<p className="portrait-prompt">{job.effective_prompt || job.prompt}</p><small>{job.model} · 本地任务 {job.id} · 远端 {job.task_id || "尚未返回"} · 角色版本 {job.source_version}</small></details>
        </article>)}
        <small>自动查询最多约四分钟；关闭窗口后停止查询，不取消远端生成。图片保存到本地后才可选用；下载失败只重试下载。</small>
      </section>
    </div>
  </Modal>;
}
