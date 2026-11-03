import { useEffect, useRef, useState } from "react";
import { api, errorMessage, postJson } from "./api";
import { Callout, Field, Modal } from "./components";
import type { Storyboard } from "./types";

type Row = { shot_id: string; label: string; error: string | null; skip: boolean; needs_keyframe?: boolean; reuse_keyframe?: boolean; references?: { name: string }[] };
type Batch = { id: string; kind: string; active: boolean; fallback_active?: boolean; items: { id: string; shot_id: string; label: string; stage: string; status: string; provider?: string; result_id: string | null; error: string | null }[] };
type Request = { kind: "keyframes" | "videos" | "workflow"; shot_ids: string[]; model: string; resolution: string; aspect_ratio: string; audio: string; image_count: number; image_resolution: string; reuse_selected: boolean; use_character_bindings: boolean; fallback_provider: "none" | "seedance"; request_token: string };
const names: Record<string, string> = { queued: "排队中", submitting: "正在提交", waiting: "等待首帧", waiting_keyframe: "等待首帧完成后自动提交视频", succeeded: "首帧已选第一张", submitted: "视频已提交，等待成片", ready: "视频已持久化", failed: "未完成", unknown: "结果待核对，勿重提", skipped: "已跳过" };

export function BatchProductionPanel({ board, onChanged, onShot, notify }: { board: Storyboard; onChanged: () => void; onShot: (id: string) => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const shots = board.scenes.flatMap(scene => scene.shots.map(shot => ({ ...shot, label: shot.visual_description.match(/^\[(S\d+[A-Z]?)\]/)?.[1] || `SC ${scene.order_index} / SH ${shot.order_index}` })));
  const [selected, setSelected] = useState<string[]>([]);
  const [model, setModel] = useState("kling-3.0");
  const [resolution, setResolution] = useState("720p");
  const [ratio, setRatio] = useState("16:9");
  const [audio, setAudio] = useState("native");
  const [count, setCount] = useState(1);
  const [imageResolution, setImageResolution] = useState("1k");
  const [reuse, setReuse] = useState(true);
  const [fallback, setFallback] = useState(true);
  const [useBindings, setUseBindings] = useState(false);
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [plan, setPlan] = useState<{ request: Request; preview_hash: string; rows: Row[] } | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const changed = useRef(onChanged); changed.current = onChanged;
  const wasActive = useRef(false);
  const locked = board.status === "revising";
  const endpoint = `/episodes/${board.id}/production-batches`;
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const data = await api<Batch[]>(endpoint);
        if (cancelled) return;
        setBatches(data);
        const active = data.some(b => b.active);
        if (wasActive.current && !active) changed.current();
        wasActive.current = active;
      } catch { /* Polling is read-only; manual refresh remains available. */ }
      if (!cancelled) timer = setTimeout(() => void load(), 5000);
    }
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [endpoint]);
  async function preview(kind: Request["kind"]) {
    if (kind !== "keyframes" && selected.filter(id => shots.some(s => s.id === id)).length > 3) {
      return notify("每批最多 3 个视频镜头，请审核成片后再提交下一批", "error");
    }
    setBusy(true); setAccepted(false); setUncertain(false);
    const request: Request = { kind, shot_ids: selected.filter(id => shots.some(s => s.id === id)), model, resolution, aspect_ratio: ratio, audio, image_count: count, image_resolution: imageResolution, reuse_selected: reuse, use_character_bindings: useBindings, fallback_provider: kind === "keyframes" || !fallback ? "none" : "seedance", request_token: crypto.randomUUID() };
    try {
      const result = await postJson<{ rows: Row[]; preview_hash: string }>(endpoint + "/preview", request);
      setPlan({ ...result, request });
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function submit() {
    if (!plan || !accepted || busy) return;
    setBusy(true);
    try {
      const result = await postJson<Batch>(endpoint, { ...plan.request, preview_hash: plan.preview_hash, confirmed: true });
      setBatches(items => [result, ...items.filter(b => b.id !== result.id)]); wasActive.current = result.active;
      setPlan(null); setUncertain(false); notify("批量任务已入队，后台继续执行；可在本页查看进度");
    } catch (error) { setUncertain(true); notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function refresh() {
    try { setBatches(await api<Batch[]>(endpoint)); onChanged(); } catch (error) { notify(errorMessage(error), "error"); }
  }
  async function stop(id: string) {
    if (!window.confirm("停止尚未提交的任务及后续自动视频？已提交的首帧或视频继续执行，不会退款。")) return;
    try { await postJson(`/production-batches/${id}/stop`, {}); await refresh(); } catch (error) { notify(errorMessage(error), "error"); }
  }
  const readyRows = plan?.rows.filter(row => !row.error && !row.skip) || [];
  const workflow = plan?.request.kind === "workflow";
  const imageTasks = readyRows.filter(row => row.needs_keyframe).length * (plan?.request.image_count || 1);
  return <section className="panel page-stack batch-production">
    <div className="panel-heading"><h3>批量制作 · 可灵优先</h3><span>已选 {selected.filter(id => shots.some(s => s.id === id)).length} / {shots.length} 镜头</span></div>
    <Callout>小批量审核：默认 720p，每批最多 3 个视频镜头。每批完成后先审核成片，再手动选择并提交下一批。</Callout>
    <p className="muted">可分步生成首帧和视频，或选择完整工作流：每镜首帧完成并选第一张后，自动提交该镜视频，无需等全批首帧完成。</p>
    <details><summary>选择镜头（支持全选）</summary><div className="page-actions"><button className="soft-button" disabled={busy || locked} onClick={() => setSelected(shots.map(s => s.id))}>全选本集镜头</button><button className="soft-button" disabled={busy} onClick={() => setSelected([])}>取消全选</button></div>
      <div className="batch-shot-list">{shots.map(shot => <label className="batch-shot" key={shot.id}><span><input type="checkbox" disabled={busy || locked} checked={selected.includes(shot.id)} onChange={e => setSelected(ids => e.target.checked ? [...ids, shot.id] : ids.filter(id => id !== shot.id))} /> {shot.label} · {shot.shot_type} · {shot.duration}s · {shot.visual_description.slice(0,65)}</span></label>)}</div>
    </details>
    <div className="field-grid three"><Field label="每镜首帧候选数"><select value={count} disabled={busy} onChange={e => setCount(Number(e.target.value))}>{[1,2,3,4].map(n => <option key={n} value={n}>{n} 张，自动选第 1 张</option>)}</select></Field><Field label="首帧清晰度"><select value={imageResolution} disabled={busy} onChange={e => setImageResolution(e.target.value)}><option value="1k">1K</option><option value="2k">2K</option></select></Field><Field label="画幅"><select value={ratio} disabled={busy} onChange={e => setRatio(e.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option></select></Field></div>
    <label><input type="checkbox" checked={reuse} disabled={busy} onChange={e => setReuse(e.target.checked)} /> 跳过已有可用首帧的镜头（取消后重新生成，并替换已选首帧）</label>
    <div className="field-grid three"><Field label="视频模型"><select value={model} disabled={busy} onChange={e => setModel(e.target.value)}><option value="kling-3.0">Kling 3.0</option><option value="kling-3.0-turbo">Kling 3.0 Turbo</option><option value="kling-3.0-omni">Kling 3.0 Omni</option></select></Field><Field label="视频清晰度"><select value={resolution} disabled={busy} onChange={e => setResolution(e.target.value)}><option>720p</option><option>1080p</option><option>4k</option></select></Field><Field label="视频声音"><select value={audio} disabled={busy} onChange={e => setAudio(e.target.value)}><option value="native">原生音画同步</option><option value="off">无声，后期配音</option></select></Field></div>
    <p className="muted">默认配音：女主清冷、忧郁、轻声克制；男主自然年轻男声。对白和画外音按分镜原文生成，声线通过提示词约束。</p>
    <label><input type="checkbox" checked={useBindings} disabled={busy} onChange={e => setUseBindings(e.target.checked)} /> 使用已创建的可灵人物主体（可选；现有首帧可直接图生视频）</label>
    <label><input type="checkbox" checked={fallback} disabled={busy} onChange={e => setFallback(e.target.checked)} /> 可灵额度不足或重试 3 次仍失败时，本批剩余视频自动切到 Seedance 2.0</label>
    <div className="page-actions"><button className="primary-button" disabled={busy || locked || !selected.length} onClick={() => void preview("workflow")}>首帧 → 自动提交视频</button><button className="soft-button" disabled={busy || locked || !selected.length} onClick={() => void preview("keyframes")}>批量生成首帧</button><button className="primary-button" disabled={busy || locked || !selected.length} onClick={() => void preview("videos")}>批量提交视频</button><button className="soft-button" onClick={() => void refresh()}>刷新批量进度 / 首帧</button></div>
    <p className="muted">每镜成片保存后，再提交下一镜；关闭页面不取消任务。视频使用各镜头已保存的提示词与已选首帧。切换后使用 Seedance 2.0，短镜头至少生成 4 秒再剪短；超时、限流或结果不明时不会自动切换。S42 自动使用 S39 尾帧，S52 自动接 S22 成片末帧；其他未配置的首尾帧与多模态镜头会在预检中列出。视频结果在「生成历史」查看。</p>
    {batches.map(batch => <details key={batch.id} open={batch.active}><summary>{batch.kind === "workflow" ? "完整工作流" : batch.kind === "keyframes" ? "首帧批次" : "视频批次"}{batch.fallback_active ? " · 已切 Seedance 2.0" : ""} · {batch.items.filter(i => !["queued","submitting","waiting","waiting_keyframe","submitted"].includes(i.status)).length}/{batch.items.length} 阶段处理完毕 · {batch.id}</summary>{batch.active && <button className="text-button" onClick={() => void stop(batch.id)}>停止尚未提交的任务</button>}{batch.items.map(item => <div className="panel-heading" key={item.id}><span>{item.label} · {item.stage === "keyframes" ? "首帧" : `视频${item.provider === "seedance" ? " / Seedance 2.0" : " / 可灵"}`} · {names[item.status] || item.status}{item.error ? ` · ${item.error}` : ""}</span><button className="text-button" onClick={() => onShot(item.shot_id)}>查看镜头 / 候选</button></div>)}</details>)}
    {plan && <Modal title={workflow ? "确认完整工作流：首帧 → 视频" : plan.request.kind === "keyframes" ? "确认批量生成首帧" : "确认批量提交视频"} onClose={() => !busy && setPlan(null)} wide>
      <p>已选 {plan.rows.length} 个镜头：可提交 {readyRows.length}，预检未通过 {plan.rows.filter(row => row.error).length}，跳过 {plan.rows.filter(row => !row.error && row.skip).length}。未通过项不会产生本批次生成费用。</p>
      <Callout tone="warning">本次将提交 {readyRows.length} 个镜头{workflow ? `，生成 ${imageTasks} 张首帧候选，随后自动提交最多 ${readyRows.length} 条视频；${plan.request.model}，${plan.request.resolution}，${plan.request.audio === "native" ? "原生音画同步" : "无声"}` : plan.request.kind === "keyframes" ? `，共 ${readyRows.length * plan.request.image_count} 张首帧候选，自动选每镜第 1 张（索引 0），不会自动选第二张补位` : `，模型 ${plan.request.model}，${plan.request.resolution}，${plan.request.audio === "native" ? "原生音画同步" : "无声"}`}。实际费用以所使用的 API 平台账单为准；不是网页会员额度。失败或不采用素材不代表退款。</Callout>
      <p>{workflow ? "一次确认同时授权首帧和后续视频费用。新首帧自动选第 1 张（索引 0），失败不改选第二张且不提交该镜视频；已有可用首帧按设置复用，文生视频镜头直接提交、不生成首帧。中途修改镜头或资产会阻止后续视频。已在其他批次运行的镜头不会重复提交。" : "预检失败项不会提交；已有可用首帧且选择跳过的项不会生成。"}视频会新建候选片段，即使该镜头已有历史视频。</p>
      {plan.request.fallback_provider === "seedance" && <Callout tone="warning">可灵明确返回余额或资源包不足，或首次尝试及 3 次重试均失败后，将自动改由 Seedance 2.0 提交该镜和剩余视频，产生 Seedance API 费用。限流、超时和状态不明时不会切换；首帧生成失败也不会切换。短镜头在 Seedance 至少生成 4 秒。</Callout>}
      <div style={{ maxHeight: "35vh", overflow: "auto" }}>{plan.rows.map(row => <p key={row.shot_id}>{row.label}：{row.error || (row.skip ? "跳过已有首帧" : workflow ? row.needs_keyframe ? "生成首帧 → 选第 1 张 → 自动提交视频" : row.reuse_keyframe ? "复用已选首帧 → 提交视频" : "文生视频，直接提交" : "可提交")}{!row.skip && !row.error && (!workflow || row.needs_keyframe) && row.references?.length ? ` · 将发送母版：${row.references.map(r => r.name).join("、")}` : ""}</p>)}</div>
      <label className="batch-consent"><input type="checkbox" checked={accepted} disabled={busy} onChange={e => setAccepted(e.target.checked)} /> 我确认本次数量和参数，有权传输所选角色/场景母版及提示词，并同意费用{workflow ? "，授权自动选用第一张候选并继续提交视频，不再逐镜确认" : plan.request.kind === "keyframes" ? "和自动选用第一张候选" : ""}。</label>
      {uncertain && <Callout tone="warning">提交未得到确认，请先刷新批量进度核对。再次点下方按钮使用同一提交标识找回批次，不会重复建立该批次；不要另建一批重试。</Callout>}
      <div className="modal-actions"><button className="ghost-button" disabled={busy} onClick={() => setPlan(null)}>返回</button><button className="primary-button" disabled={busy || !accepted || !readyRows.length} onClick={() => void submit()}>{busy ? "正在入队…" : uncertain ? "核对原提交" : "确认并开始批量任务"}</button></div>
    </Modal>}
  </section>;
}
