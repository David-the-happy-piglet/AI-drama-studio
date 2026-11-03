import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, mediaUrl, postJson } from "./api";
import { Callout, Field, Modal } from "./components";
import { keyframePending, keyframePollable, keyframePrompt } from "./keyframes";
import type { KeyframeJob, KeyframeStudio, SelectedKeyframe } from "./keyframes";
import { portraitStatus } from "./portraitJobs";
import type { Shot } from "./types";

type Submission = { expected_version: number; context_hash: string; request_token: string;
  prompt: string; negative_prompt: string; count: number; resolution: string; aspect_ratio: string;
  confirmed: true; references_confirmed: boolean };

export function ShotKeyframeModal({ shot, initialRatio, onClose, onSelected }: {
  shot: Shot; initialRatio: string; onClose: () => void;
  onSelected: (shot: Shot, selected: SelectedKeyframe) => void;
}) {
  const [studio, setStudio] = useState<KeyframeStudio | null>(null);
  const [jobs, setJobs] = useState<KeyframeJob[]>([]);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [count, setCount] = useState(1);
  const [resolution, setResolution] = useState("1k");
  const [ratio, setRatio] = useState(["16:9", "9:16", "1:1"].includes(initialRatio) ? initialRatio : "16:9");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingRequest, setPendingRequest] = useState<Submission | null>(null);
  const [chosen, setChosen] = useState<{ job: string; index: number } | null>(null);
  const working = useRef(false);
  const initialized = useRef(false);
  const base = `/shots/${shot.id}`;
  const reference = Boolean(studio?.references.length);
  const effective = keyframePrompt(studio?.reference_prefix || "", prompt, negative, reference);
  const active = jobs.some(keyframePending);
  const locked = busy || Boolean(pendingRequest);
  const canSubmit = Boolean(studio?.configured && !studio.errors.length && prompt.trim() && effective.length <= 2500);
  const pollKey = jobs.filter(keyframePollable).map(job => job.id).sort().join(",");
  const chosenJob = jobs.find(job => job.id === chosen?.job);
  const candidate = chosenJob?.candidates.find(c => c.index === chosen?.index);

  function merge(next: KeyframeJob) {
    setJobs(current => [next, ...current.filter(job => job.id !== next.id)].sort((a, b) => b.created_at.localeCompare(a.created_at)));
  }
  useEffect(() => {
    let cancelled = false;
    api<KeyframeStudio>(`${base}/keyframe-studio`).then(data => {
      if (cancelled) return;
      setStudio(data); setJobs(data.jobs); setPrompt(data.prompt); setNegative(data.negative_prompt); initialized.current = true;
    }).catch(reason => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [base]);

  useEffect(() => {
    if (!pollKey) return;
    let cancelled = false, attempts = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!working.current) {
        for (const id of pollKey.split(",")) {
          if (cancelled) return;
          try {
            const next = await postJson<KeyframeJob>(`${base}/keyframe-jobs/${id}/refresh`, {});
            if (!cancelled) merge(next);
          } catch (reason) { if (!cancelled) setError(errorMessage(reason)); }
        }
      }
      if (!cancelled && ++attempts < 24) timer = setTimeout(() => void poll(), 10000);
    }
    timer = setTimeout(() => void poll(), 3000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [base, pollKey]);

  async function load() {
    if (working.current) return;
    working.current = true; setBusy(true); setError("");
    try {
      const next = await api<KeyframeStudio>(`${base}/keyframe-studio`);
      setStudio(next); setJobs(next.jobs); setConfirmed(false);
      if (!initialized.current) { setPrompt(next.prompt); setNegative(next.negative_prompt); initialized.current = true; }
      if (pendingRequest && next.jobs.some(job => job.request_token === pendingRequest.request_token)) setPendingRequest(null);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { working.current = false; setBusy(false); }
  }

  async function submit(recover = false) {
    if (working.current || !studio || (!recover && (!confirmed || active || !canSubmit))) return;
    const body: Submission | null = recover ? pendingRequest : {
      expected_version: studio.shot_version, context_hash: studio.context_hash, request_token: crypto.randomUUID(),
      prompt, negative_prompt: negative, count, resolution, aspect_ratio: ratio, confirmed: true, references_confirmed: reference,
    };
    if (!body) return;
    working.current = true; setBusy(true); setError(""); setPendingRequest(body);
    try {
      merge(await postJson<KeyframeJob>(`${base}/keyframe-jobs`, body));
      setPendingRequest(null); setConfirmed(false);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status < 500 && reason.status !== 408) setPendingRequest(null);
      setError(errorMessage(reason));
    } finally { working.current = false; setBusy(false); }
  }
  async function refresh(job: KeyframeJob) {
    if (working.current) return;
    working.current = true; setBusy(true); setError("");
    try { merge(await postJson<KeyframeJob>(`${base}/keyframe-jobs/${job.id}/refresh`, {})); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { working.current = false; setBusy(false); }
  }
  async function adopt() {
    if (working.current || !studio || !chosenJob || !candidate?.path) return;
    working.current = true; setBusy(true); setError("");
    try {
      const result = await postJson<{ shot: Shot; selected: SelectedKeyframe }>(`${base}/keyframe-jobs/${chosenJob.id}/select`, {
        expected_version: studio.shot_version, candidate_index: candidate.index, confirmed: true,
      });
      onSelected(result.shot, result.selected);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { working.current = false; setBusy(false); }
  }

  return <Modal title="自动生成镜头首帧" eyebrow={`KEYFRAME / ${shot.id}`} wide onClose={() => { if (!working.current) onClose(); }}>
    <div className="portrait-studio keyframe-studio">
      <section className="modal-form">
        <Callout>{reference ? "基于已关联的角色与场景母版生成镜头画面，实际发送下方图片。人物参考身份，场景参考布局、家具及光线。" : "当前镜头没有角色或场景参考图：只根据文字生成首帧。可先关联角色，并在分镜台为场景选择母版。"} 不自动生成视频，也不改角色母版。</Callout>
        {studio?.references.map(ref => <div className="current-portrait" key={`${ref.character_id || ref.scene_asset_id}:${ref.path}`}>
          {ref.path && !ref.error && <img src={mediaUrl(ref.path) || ""} alt={`将发送给可灵：${ref.name}母版`} />}
          <div><strong>{ref.kind === "scene" ? "场景 · " : "角色 · "}{ref.name}</strong><small>{ref.error || `母版 SHA-256：${ref.sha256?.slice(0, 12)}`}</small></div>
        </div>)}
        {studio?.errors.map(message => <Callout key={message} tone="warning">{message}</Callout>)}
        <Field label="首帧提示词" hint={studio?.prompt_source || "正在读取当前镜头…"}>
          <textarea rows={8} maxLength={2500} value={prompt} disabled={locked || !studio} onChange={e => { setPrompt(e.target.value); setConfirmed(false); }} />
          <small>实际提示词 {effective.length}/2500 字；保留单幅静态构图，动作过程留给视频。</small>
        </Field>
        <Field label="首帧负向约束"><textarea rows={2} maxLength={2500} value={negative} disabled={locked} onChange={e => { setNegative(e.target.value); setConfirmed(false); }} /></Field>
        <details><summary>查看实际发送的完整提示词</summary><p className="portrait-prompt">{effective}</p></details>
        <div className="field-grid three">
          <Field label="候选张数"><select disabled={locked} value={count} onChange={e => { setCount(Number(e.target.value)); setConfirmed(false); }}>{[1, 2, 3, 4].map(n => <option value={n} key={n}>{n} 张</option>)}</select></Field>
          <Field label="首帧清晰度"><select disabled={locked} value={resolution} onChange={e => { setResolution(e.target.value); setConfirmed(false); }}><option value="1k">1K</option><option value="2k">2K</option></select></Field>
          <Field label="首帧画幅"><select disabled={locked} value={ratio} onChange={e => { setRatio(e.target.value); setConfirmed(false); }}><option>16:9</option><option>9:16</option><option>1:1</option></select></Field>
        </div>
        {studio && !studio.configured && <Callout tone="warning">{studio.configuration_issues.join("；")}</Callout>}
        {active && <Callout>本镜头已有待完成或待核实的首帧任务。请查询原任务，不重复提交。</Callout>}
        <label className="quota-confirm"><input type="checkbox" checked={confirmed} disabled={locked || active || !canSubmit} onChange={e => setConfirmed(e.target.checked)} />我确认{reference ? "有权使用并将上方角色/场景母版图片和提示词发送给可灵 Image 3.0 Omni" : "将提示词发送给可灵 Image 3.0，不使用角色或场景参考图"}，生成 {count} 张 {resolution.toUpperCase()} 首帧候选。实际费用以可灵 API 账单为准。</label>
        <button type="button" className="primary-button" disabled={locked || active || !canSubmit || !confirmed} onClick={() => void submit()}>生成 {count} 张首帧候选</button>
        {pendingRequest && <Callout tone="warning">提交结果尚未确认，请先刷新历史；恢复会复用同一提交标识。<button type="button" disabled={busy} className="soft-button" onClick={() => void submit(true)}>恢复本次提交</button></Callout>}
        {error && <div role="alert"><Callout tone="warning">{error}</Callout></div>}
      </section>
      <section className="modal-form">
        <div className="panel-heading"><h3>首帧候选与历史</h3><button type="button" className="soft-button" disabled={busy} onClick={() => void load()}>刷新首帧列表</button></div>
        {studio?.selected && <div className="current-portrait"><img src={mediaUrl(studio.selected.path) || ""} alt="本镜头当前首帧" /><div><strong>当前首帧</strong><p>{studio.selected.error || "已保存，新候选图不会自动替换它。"}</p></div></div>}
        {candidate?.path && chosenJob && <div className="portrait-selection">
          <img src={mediaUrl(candidate.path) || ""} alt={`首帧候选 ${candidate.index + 1} 放大预览`} />
          <p>采用后替换本镜头当前首帧，旧候选图保留；不会修改角色资产或提交视频。</p>
          <button type="button" className="primary-button" disabled={locked || chosenJob.context_hash !== studio?.context_hash} onClick={() => void adopt()}>确认选用此图作为首帧</button>
          {chosenJob.context_hash !== studio?.context_hash && <Callout tone="warning">镜头或母版已变化，此候选已过期，请重新生成。</Callout>}
        </div>}
        {!jobs.length && <p>还没有生成首帧。检查左侧画面要求后确认提交。</p>}
        {jobs.map(job => <article className="portrait-batch" key={job.id}>
          <div className="panel-heading"><div><strong>{portraitStatus[job.status] || job.status} · {job.references_snapshot.length ? "基于参考母版" : "纯文字空镜"}</strong><small>{new Date(job.created_at).toLocaleString()} · {job.count} 张 · {job.aspect_ratio} · {job.resolution}</small></div><button type="button" className="text-button" disabled={busy} onClick={() => void refresh(job)}>查询 / 重试下载</button></div>
          {job.error_message && <p className="portrait-error">{job.error_message}</p>}
          <div className="portrait-candidates">{job.candidates.map(c => <div key={c.index}>{c.path ? <button type="button" className={chosen?.job === job.id && chosen.index === c.index ? "portrait-option selected" : "portrait-option"} onClick={() => setChosen({ job: job.id, index: c.index })}><img src={mediaUrl(c.path) || ""} alt={`首帧候选 ${c.index + 1}`} /><span>预览候选 {c.index + 1}</span></button> : <p>候选 {c.index + 1}：{c.error || "等待下载"}</p>}</div>)}</div>
          <details><summary>本次输入与任务信息</summary><small>{job.id} · {job.model} · 远端 {job.task_id || "待返回"}</small>{job.references_snapshot.map(ref => <div className="current-portrait" key={`${ref.character_id || ref.scene_asset_id}:${ref.path}`}><img src={mediaUrl(ref.snapshot_path || null) || ""} alt={`${ref.name}输入快照`} /><small>{ref.name} · SHA-256：{ref.sha256}</small></div>)}<p className="portrait-prompt">{job.effective_prompt}</p></details>
        </article>)}
        <small>自动查询最多约四分钟；关闭窗口不取消远端任务，可稍后打开查询。下载失败只重试下载，不会重新生成。</small>
      </section>
    </div>
  </Modal>;
}
