import { CheckCircle2, Coins, FileAudio, FileImage, FileVideo, Film, LoaderCircle, ShieldAlert, Sparkles, Upload } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, errorMessage, mediaUrl, postJson, uploadAsset } from "../api";
import { Callout, Empty, Field, Modal, SubmitButton } from "../components";
import { ShotCharactersModal } from "../ShotCharactersModal";
import { ShotKeyframeModal } from "../ShotKeyframeModal";
import { selectedKeyframeToken } from "../keyframes";
import type { KeyframeStudio, SelectedKeyframe } from "../keyframes";
import type { CharacterBindingPreview, Episode, Generation, Project, SeedanceAccount, Shot, Storyboard, VideoProvider } from "../types";

const modeNames: Record<string, string> = {
  text_to_video: "文生视频",
  image_to_video: "首帧生视频",
  first_last_frame: "首尾帧生视频",
  reference_to_video: "多模态参考生视频",
  video_extension: "视频延长（当前适配器暂不支持）",
};

export function GeneratePage({
  project,
  preselectedShotId,
  preselectedEpisodeId,
  notify,
  onSubmitted,
}: {
  project?: Project;
  account: SeedanceAccount | null;
  preselectedShotId: string;
  preselectedEpisodeId: string;
  notify: (message: string, kind?: "success" | "error") => void;
  onSubmitted: (generation: Generation) => void;
}) {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [episodeId, setEpisodeId] = useState("");
  const [board, setBoard] = useState<Storyboard | null>(null);
  const revisionLocked = board?.status === "revising";
  const [shotId, setShotId] = useState(preselectedShotId);
  const [model, setModel] = useState("kling-3.0");
  const [provider, setProvider] = useState("kling");
  const minimax = provider === "minimax";
  const providerLabel = minimax ? "MiniMax Token Plan" : provider === "seedance" ? "火山方舟 API" : "可灵 API";
  const [materialPurpose, setMaterialPurpose] = useState("broll");
  const [quotaConfirmed, setQuotaConfirmed] = useState(false);
  const turbo = provider === "kling" && model === "kling-3.0-turbo";
  const omni = provider === "kling" && model === "kling-3.0-omni";
  const klingModes = omni ? ["text_to_video", "image_to_video", "first_last_frame", "reference_to_video"] : turbo ? ["text_to_video", "image_to_video"] : ["text_to_video", "image_to_video", "first_last_frame"];
  const [providers, setProviders] = useState<VideoProvider[]>([]);
  const [nativeAudio, setNativeAudio] = useState(true);
  const [elementId, setElementId] = useState("");
  const [elementAlias, setElementAlias] = useState("主角");
  const [autoBind, setAutoBind] = useState(false);
  const [bindingPreview, setBindingPreview] = useState<CharacterBindingPreview | null>(null);
  const [editingShot, setEditingShot] = useState<Shot | null>(null);
  const [keyframeShot, setKeyframeShot] = useState<Shot | null>(null);
  const [savedKeyframe, setSavedKeyframe] = useState<SelectedKeyframe | null>(null);
  const currentProvider = providers.find(item => item.provider === provider);
  const ready = Boolean(currentProvider?.configured);
  useEffect(() => { void api<VideoProvider[]>("/providers/video").then(setProviders).catch(error => notify(errorMessage(error), "error")); }, []);
  const [resolution, setResolution] = useState("720p");
  const [ratio, setRatio] = useState("16:9");
  const [duration, setDuration] = useState(5);
  const [prompt, setPrompt] = useState("");
  const [sourcePrompts, setSourcePrompts] = useState<Record<string, { source_shot_id: string; fields: Record<string, string> }>>({});
  const [negativePrompt, setNegativePrompt] = useState("");
  const [firstFrame, setFirstFrame] = useState<File | null>(null);
  const [lastFrame, setLastFrame] = useState<File | null>(null);
  const [references, setReferences] = useState<File[]>([]);
  const [referenceVideo, setReferenceVideo] = useState<File | null>(null);
  const [referenceVideoUrl, setReferenceVideoUrl] = useState("");
  const [audio, setAudio] = useState<File | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const pendingSubmission = useRef<{
    requestToken: string;
    payload: Record<string, unknown> | null;
  } | null>(null);

  useEffect(() => {
    if (!project) { setEpisodes([]); setEpisodeId(""); return; }
    api<Episode[]>(`/projects/${project.id}/episodes`).then((data) => {
      setEpisodes(data);
      setEpisodeId((current) => data.some((item) => item.id === preselectedEpisodeId) ? preselectedEpisodeId : data.some((item) => item.id === current) ? current : data[0]?.id || "");
    }).catch((error) => notify(errorMessage(error), "error"));
  }, [project?.id]);

  useEffect(() => {
    setSourcePrompts({});
    if (!project) return;
    let active = true;
    void fetch(`/media/projects/${encodeURIComponent(project.id)}/storyboard_details.json`, { cache: "no-store" })
      .then(response => response.ok ? response.json() as Promise<{ shots: Record<string, { source_shot_id: string; fields: Record<string, string> }> }> : null)
      .then(data => { if (active && data) setSourcePrompts(data.shots); })
      .catch(() => {});
    return () => { active = false; };
  }, [project?.id]);

  useEffect(() => {
    if (!episodeId) return setBoard(null);
    api<Storyboard>(`/episodes/${episodeId}/storyboard`).then(setBoard).catch((error) => notify(errorMessage(error), "error"));
  }, [episodeId]);

  const shots = useMemo(() => board?.scenes.flatMap((scene) => scene.shots.map((shot) => ({ ...shot, sceneLabel: `SC ${String(scene.order_index).padStart(2, "0")} · ${scene.location_description}` }))) || [], [board]);
  const selectedShot = shots.find((shot) => shot.id === shotId) as (Shot & { sceneLabel: string }) | undefined;
  const supportedMode = selectedShot && (provider === "kling" ? klingModes : currentProvider?.generation_modes || []).includes(selectedShot.generation_mode);
  const automaticCount = autoBind ? bindingPreview?.elements.length || 0 : 0;
  const frameToken = selectedKeyframeToken(Boolean(firstFrame), savedKeyframe, selectedShot?.generation_mode || "");

  useEffect(() => {
    let cancelled = false;
    setSavedKeyframe(null);
    if (selectedShot) api<KeyframeStudio>(`/shots/${selectedShot.id}/keyframe-studio`)
      .then(data => { if (!cancelled) setSavedKeyframe(data.selected); })
      .catch(error => { if (!cancelled) notify(`首帧读取失败：${errorMessage(error)}`, "error"); });
    return () => { cancelled = true; };
  }, [selectedShot?.id, selectedShot?.version]);

  useEffect(() => {
    let cancelled = false;
    setBindingPreview(null);
    if (provider === "kling" && selectedShot) {
      api<CharacterBindingPreview>(`/shots/${selectedShot.id}/character-bindings/preview?model=${encodeURIComponent(model)}`)
        .then(data => { if (!cancelled) setBindingPreview(data); })
        .catch(error => { if (!cancelled) setBindingPreview({ supported: false, elements: [], errors: [errorMessage(error)] }); });
    }
    return () => { cancelled = true; };
  }, [selectedShot?.id, selectedShot?.version, provider, model]);

  useEffect(() => {
    const preferred = shots.find((shot) => shot.id === preselectedShotId)?.id;
    setShotId((current) => preferred || (shots.some((shot) => shot.id === current) ? current : shots[0]?.id || ""));
  }, [shots, preselectedShotId]);

  useEffect(() => {
    if (!selectedShot) return;
    setNegativePrompt(selectedShot.negative_prompt || "");
    setDuration(minimax ? 6 : Math.max(provider === "kling" ? 3 : 4, Math.min(15, Math.round(selectedShot.duration))));
    setFirstFrame(null); setLastFrame(null); setReferences([]); setReferenceVideo(null); setReferenceVideoUrl(""); setAudio(null); setNativeAudio(true);
    setElementId(""); setElementAlias("主角"); setAutoBind(false); setConfirm(false);
    pendingSubmission.current = null;
  }, [selectedShot?.id]);

  useEffect(() => {
    if (!selectedShot) return;
    const fields = sourcePrompts[selectedShot.id]?.fields;
    let nextPrompt = provider === "kling"
      ? fields?.["可灵提示词"] || selectedShot.generation_prompt || selectedShot.visual_description
      : fields?.["Seedance提示词"] || selectedShot.generation_prompt || selectedShot.visual_description;
    if (nativeAudio && fields && project?.id === "SECOND_PASS") {
      const dialogue = Object.entries(fields).filter(([key, value]) => key.startsWith("对白") && !["无", "无。"].includes(value));
      nextPrompt += "\n原生音频：女主使用清冷、忧郁、轻柔克制的年轻女声，普通话自然清晰，略带气声；回忆中按台词需要自然明快，维持同一声线。男主使用自然随意的年轻男声。\n";
      nextPrompt += dialogue.length ? dialogue.map(([key, value]) => `${key}：${value}`).join("\n") + "\n只说上述台词；画外音不让画面人物对口型。严格保留停顿与沉默，不添加其他对白。" : "本镜没有台词，不添加人声或旁白。";
      if (fields["环境音"]) nextPrompt += "\n环境音：" + fields["环境音"];
    }
    setPrompt(nextPrompt);
  }, [selectedShot?.id, provider, sourcePrompts, nativeAudio, project?.id]);

  function requestConfirmation(event: FormEvent) {
    event.preventDefault();
    if (!selectedShot) return notify("请先选择镜头", "error");
    if (!ready || !supportedMode) return notify("该平台未配置或所选模型不支持此生成方式", "error");
    if (provider === "kling" && autoBind && (!bindingPreview || bindingPreview.errors.length)) return notify(bindingPreview?.errors.join("；") || "正在检查角色绑定，请稍候", "error");
    if (provider === "kling" && automaticCount + (elementId ? 1 : 0) > 3) return notify("自动与手动主体合计最多三个", "error");
    if (minimax && (duration !== 6 && duration !== 10 || resolution === "1080P" && duration !== 6)) return notify("MiniMax 支持 768P 6/10 秒，或 1080P 6 秒", "error");
    if (provider === "seedance" && (duration < 4 || duration > 15)) return notify("Seedance 2.0 支持 4–15 秒，请调整时长", "error");
    if (provider === "seedance" && selectedShot.generation_mode === "reference_to_video" && !references.length && !referenceVideoUrl.trim()) return notify("Seedance 2.0 需要参考图片或视频链接，不能只用音频", "error");
    if (provider === "seedance" && referenceVideoUrl.trim() && !/^(https?:\/\/|asset:\/\/)/.test(referenceVideoUrl.trim())) return notify("参考视频请填写公网 URL 或 asset:// 素材 ID", "error");
    if (provider === "kling" && (duration < 3 || duration > 15)) return notify("可灵 3.0 支持 3–15 秒，请调整时长", "error");
    if (selectedShot.generation_mode === "image_to_video" && !firstFrame && !frameToken) return notify("请先自动生成并选用首帧，或手动上传首帧图片", "error");
    if (selectedShot.generation_mode === "first_last_frame" && ((!firstFrame && !frameToken) || !lastFrame)) return notify("首尾帧模式需要已选或上传的首帧，以及尾帧图片", "error");
    if (selectedShot.generation_mode === "reference_to_video" && !references.length && !referenceVideo && !referenceVideoUrl.trim() && !audio && !(omni && (elementId || automaticCount))) return notify("请至少添加一个参考素材或已绑定角色", "error");
    if (turbo && (resolution === "4k" || lastFrame)) return notify("Turbo 不支持 4K 或尾帧，请清除尾帧或切换模型", "error");
    if (omni && selectedShot.generation_mode === "reference_to_video") {
      if (referenceVideo || audio) return notify("当前 Omni 仅接图片/主体参考，请清除视频和音频，或切换 Seedance", "error");
      if (references.length + automaticCount + (elementId ? 1 : 0) > 4) return notify("Omni 图片与主体参考合计最多 4 个", "error");
    }
    setQuotaConfirmed(false);
    pendingSubmission.current = {
      requestToken: crypto.randomUUID(),
      payload: null,
    };
    setConfirm(true);
  }

  async function submitGeneration() {
    if (!project || !selectedShot) return;
    if (minimax && !quotaConfirmed) return;
    const pending = pendingSubmission.current;
    if (!pending) return;
    setBusy(true);
    try {
      let requestPayload = pending.payload;
      if (!requestPayload) {
        const frameMode = ["image_to_video", "first_last_frame"].includes(selectedShot.generation_mode);
        const referenceMode = selectedShot.generation_mode === "reference_to_video";
        const first = frameMode && firstFrame ? await uploadAsset(project.id, "shot_keyframe", firstFrame) : null;
        const last = (selectedShot.generation_mode === "first_last_frame" || (frameMode && provider === "kling")) && lastFrame ? await uploadAsset(project.id, "shot_keyframe", lastFrame) : null;
        const uploadedReferences = await Promise.all((referenceMode ? references : []).map((file) => uploadAsset(project.id, "reference_image", file)));
        const uploadedVideo = referenceMode && referenceVideo ? await uploadAsset(project.id, "reference_video", referenceVideo) : null;
        const uploadedAudio = referenceMode && audio ? await uploadAsset(project.id, "reference_audio", audio) : null;
        requestPayload = {
          request_token: pending.requestToken,
          confirmed: true,
          provider,
          model,
          prompt,
          negative_prompt: negativePrompt,
          duration,
          resolution,
          aspect_ratio: provider === "seedance" && frameMode ? "adaptive" : ratio,
          use_character_bindings: autoBind,
          first_frame: first?.path ?? null,
          keyframe_selection_token: frameMode && !first ? frameToken : null,
          last_frame: last?.path ?? null,
          references: uploadedReferences.map((item) => item.path),
          reference_videos: referenceMode && provider === "seedance" && referenceVideoUrl.trim() ? [referenceVideoUrl.trim()] : uploadedVideo ? [uploadedVideo.path] : [],
          reference_audios: uploadedAudio ? [uploadedAudio.path] : [],
          api_parameters: minimax ? { token_plan_confirmed: quotaConfirmed, material_purpose: materialPurpose } : provider === "seedance" ? { generate_audio: nativeAudio, watermark: false } : { audio: turbo || nativeAudio ? "native" : "off", elements: !turbo && elementId && (frameMode || (omni && referenceMode)) ? [{ element_id: elementId, id: elementAlias }] : [] },
        };
        pending.payload = requestPayload;
      }
      const generation = await postJson<Generation>(`/shots/${selectedShot.id}/generations`, requestPayload);
      setConfirm(false);
      pendingSubmission.current = null;
      notify(generation.error_message || `任务已提交给 ${providerLabel}`, generation.error_message ? "error" : "success");
      onSubmitted(generation);
    } catch (error) {
      const message = errorMessage(error);
      notify(message.includes("AigcComplianceConfirmationRequired") ? "需先在即梦网页完成一次视频生成和合规确认" : message, "error");
    } finally { setBusy(false); }
  }

  if (!project) return <Empty icon={<Sparkles size={32} />} title="请先选择项目" copy="生成任务必须绑定到项目和具体镜头。" />;

  return (
    <div className="page-stack">
      <section className="page-intro compact">
        <div><span className="eyebrow">SHOT PRODUCTION</span><h2>镜头生成</h2><p>为每个镜头选择可灵、Seedance 或 MiniMax，先预览候选片段，再决定是否用于成片。</p></div>
        <div className={ready ? "account-badge online" : "account-badge"}><span /><div><small>{provider.toUpperCase()}</small><strong>{ready ? "配置就绪" : "待配置"}</strong></div></div>
      </section>

      {revisionLocked && <Callout tone="warning">本集已撤回修订，请到「故事与审阅」重新批准分镜后再生成。现有素材和生成历史继续保留。</Callout>}
      {!ready && <Callout tone="warning">{provider === "seedance" ? "请在 ai-drama-studio/.env.local 填写 ARK_API_KEY，重启后端并刷新页面。" : minimax ? "请在后端配置 MINIMAX_TOKEN_PLAN_KEY，或引用旧项目订阅密钥文件，然后重启后端。" : "请在后端环境配置 KLING_API_KEY。可灵 API 账户与网页会员额度需分别确认。"}</Callout>}
      {selectedShot && !supportedMode && <Callout tone="warning">{minimax ? "MiniMax 当前仅支持文生/首帧生视频。请在分镜页调整该镜头的生成模式，或选择其他镜头。" : "所选模型不支持此镜头模式。Turbo 仅文生/首帧；3.0 可用首尾帧；Omni 可用图片/主体参考。"}</Callout>}
      {minimax && <Callout>适合空镜、转场、补充素材和试拍；不保证与可灵镜头的人物或风格一致。先生成独立候选片段，不会自动加入成片。声音后期添加；不满意可在剪辑软件中裁掉或不用。</Callout>}
      {omni && <Callout>Omni 当前接入文生、首尾帧和图片/主体参考；参考图在提示词中使用 @image_1、@image_2。视频编辑、视频/音频输入尚未接入。</Callout>}
      {turbo && <Callout>Turbo 仅支持 720p/1080p 和首帧，包含原生声音；当前接口没有关闭声音或强制关闭自动分镜的参数。</Callout>}

      <form className="generation-layout" onSubmit={requestConfirmation}>
        <section className="generation-main panel">
          <div className="panel-heading"><div><span className="eyebrow">01 / SOURCE SHOT</span><h3>选择镜头</h3></div><Film size={19} /></div>
          <div className="field-grid two">
            <Field label="剧集"><select value={episodeId} onChange={(event) => setEpisodeId(event.target.value)}>{episodes.map((episode) => <option value={episode.id} key={episode.id}>EP {episode.episode_number} · {episode.title}</option>)}</select></Field>
            <Field label="镜头"><select value={shotId} onChange={(event) => setShotId(event.target.value)}><option value="">选择一个镜头</option>{shots.map((shot) => <option value={shot.id} key={shot.id}>{sourcePrompts[shot.id]?.source_shot_id || `${shot.sceneLabel} / SH ${shot.order_index}`} · {shot.shot_type}</option>)}</select></Field>
          </div>
          {selectedShot && <div className="shot-source-card"><span className="mode-label">{modeNames[selectedShot.generation_mode]}</span><h3>{selectedShot.shot_type} · {selectedShot.camera_movement}</h3><p>{selectedShot.visual_description}</p><button type="button" className="soft-button" disabled={busy || revisionLocked} onClick={() => setEditingShot(selectedShot)}>关联角色（{selectedShot.characters.length}）</button></div>}

          <div className="section-rule"><span>02 / GENERATION PROMPT</span></div>
          <Field label="正向提示词" hint="按画面主体 → 动作 → 环境 → 摄影机 → 风格的顺序描述。"><textarea required rows={7} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="选择镜头后自动带入画面描述…" /></Field>
          <Field label="负向约束"><textarea rows={3} value={negativePrompt} onChange={(event) => setNegativePrompt(event.target.value)} placeholder="身份漂移、面部变形、额外手指、字幕、水印…" /></Field>

          <div className="section-rule"><span>03 / REFERENCE ASSETS</span></div>
          {selectedShot && <div className="modal-form">
            <button type="button" className="soft-button" disabled={busy || revisionLocked} onClick={() => setKeyframeShot(selectedShot)}>自动生成首帧 / 查看候选</button>
            <small>自动带入镜头画面和角色母版，确认后生成候选图；选定才保存为本镜头首帧。仅首帧 / 首尾帧模式会使用它。</small>
            {savedKeyframe && <div className="current-portrait"><img src={mediaUrl(savedKeyframe.path) || ""} alt="本镜头已选首帧" /><div><strong>本镜头已选首帧</strong><p>{savedKeyframe.error || (firstFrame ? "本次将使用你手动上传的图片，已选首帧仍保留。" : frameToken ? "视频提交将自动使用此图，无需再次上传。" : "当前模式不使用首帧；请使用首帧或首尾帧模式。")}</p></div></div>}
          </div>}
          {provider === "kling" && selectedShot && <div className="modal-form">
            <label className="quota-confirm"><input type="checkbox" checked={autoBind} onChange={e => setAutoBind(e.target.checked)} />自动引用镜头中角色的可灵主体</label>
            {autoBind && !bindingPreview && <small>正在核对角色绑定…</small>}
            {autoBind && bindingPreview?.elements.map(item => <div className="binding-details" key={item.character_id}><strong>{item.name} → @{item.id}</strong><span>主体 ID：{item.element_id}</span></div>)}
            {autoBind && bindingPreview?.errors.map(error => <Callout tone="warning" key={error}>{error}</Callout>)}
            {autoBind && bindingPreview && !bindingPreview.elements.length && !bindingPreview.errors.length && <small>此镜头未指定角色；点击上方「关联角色」选择画面中出现的人物。无人出镜可保持为空。</small>}
            {autoBind && automaticCount > 0 && <small>提交时会自动附上角色引用和身份对应说明。首帧模式需选用或上传包含这些角色的镜头首帧。</small>}
            {!autoBind && provider === "kling" && <Callout>本镜可直接使用已选首帧生成视频。需要可灵人物主体加强身份约束时，再开启自动绑定。</Callout>}
          </div>}
          {!selectedShot && <div className="asset-placeholder"><FileImage size={24} /><span>选择镜头后显示对应素材位</span></div>}
          {selectedShot?.generation_mode === "text_to_video" && <div className="asset-placeholder ready"><CheckCircle2 size={23} /><span>文生视频无需上传素材</span></div>}
          {(selectedShot?.generation_mode === "image_to_video" || selectedShot?.generation_mode === "first_last_frame") && <><div className="upload-grid"><AssetInput label="手动上传首帧" file={firstFrame} onChange={setFirstFrame} optional={Boolean(frameToken)} />{!minimax && (provider === "kling" || selectedShot.generation_mode === "first_last_frame") && <AssetInput label="尾帧图片" file={lastFrame} onChange={setLastFrame} optional={selectedShot.generation_mode !== "first_last_frame"} />}</div>{firstFrame && <button type="button" className="text-button" onClick={() => setFirstFrame(null)}>清除本次上传，使用已选首帧</button>}</>}
          {minimax && selectedShot?.generation_mode === "image_to_video" && <small>首帧小于 20 MB，短边大于 300px，长宽比 2:5–5:2；输出画幅由首帧决定。</small>}
          {selectedShot?.generation_mode === "reference_to_video" && <div className="upload-grid three"><label className="asset-input"><FileImage size={23} /><strong>参考图片</strong><span>{references.length ? `${references.length} 个文件` : "可多选，顺序会保留"}</span><input type="file" multiple accept="image/png,image/jpeg,image/webp" onChange={(event) => setReferences(Array.from(event.target.files || []))} /></label>{provider === "seedance" ? <Field label="参考视频链接（可选）" hint="公网 MP4 / MOV 链接或 asset:// 素材 ID；最长 15 秒。"><input value={referenceVideoUrl} onChange={e => setReferenceVideoUrl(e.target.value)} placeholder="https://…/reference.mp4" /></Field> : <label className="asset-input"><FileVideo size={23} /><strong>参考视频</strong><span>{referenceVideo?.name || "可选 MP4 / MOV / WebM"}</span><input type="file" accept="video/mp4,video/quicktime,video/webm" onChange={(event) => setReferenceVideo(event.target.files?.[0] ?? null)} /></label>}<label className="asset-input"><FileAudio size={23} /><strong>参考音频</strong><span>{audio?.name || (provider === "seedance" ? "可选 MP3 / WAV" : "可选 MP3 / WAV / M4A")}</span><input type="file" accept={provider === "seedance" ? "audio/mpeg,audio/wav,.mp3,.wav" : "audio/*"} onChange={(event) => setAudio(event.target.files?.[0] ?? null)} /></label></div>}
        </section>

        <aside className="generation-settings panel">
          <div className="panel-heading"><div><span className="eyebrow">OUTPUT SPECS</span><h3>生成参数</h3></div><Sparkles size={19} /></div>
          <Field label="视频平台"><select value={provider} onChange={e => { const mm = e.target.value === "minimax"; setProvider(e.target.value); setModel(mm ? "MiniMax-Hailuo-2.3" : e.target.value === "kling" ? "kling-3.0" : "doubao-seedance-2-0-260128"); setResolution(mm ? "768P" : "720p"); setRatio("16:9"); setDuration(mm ? 6 : 5); setLastFrame(null); setReferences([]); setReferenceVideo(null); setReferenceVideoUrl(""); setAudio(null); setQuotaConfirmed(false); }}><option value="seedance">Seedance · 火山方舟 API</option><option value="kling">可灵 · 官方 API</option><option value="minimax">MiniMax · Token Plan 素材</option></select></Field>
          <Field label="模型"><select value={model} onChange={(event) => { setModel(event.target.value); if (event.target.value === "kling-3.0-turbo") { setResolution("720p"); setLastFrame(null); } }}>{minimax ? <option value="MiniMax-Hailuo-2.3">Hailuo 2.3 · 空镜 / 补充素材</option> : provider === "kling" ? <><option value="kling-3.0">Kling 3.0 · 常规制作</option><option value="kling-3.0-turbo">Kling 3.0 Turbo · 快速生成</option><option value="kling-3.0-omni">Kling 3.0 Omni · 图片/主体参考</option></> : <><option value="doubao-seedance-2-0-260128">Seedance 2.0</option></>}</select></Field>
          {minimax && <Field label="素材用途"><select value={materialPurpose} onChange={e => setMaterialPurpose(e.target.value)}><option value="broll">空镜</option><option value="transition">转场素材</option><option value="supplement">补充素材</option><option value="test">试拍</option></select></Field>}
          <div className="field-grid two"><Field label="清晰度"><select value={resolution} onChange={(event) => { setResolution(event.target.value); if (minimax && event.target.value === "1080P") setDuration(6); }}>{minimax ? <><option>768P</option><option>1080P</option></> : <>{provider === "seedance" && <option>480p</option>}<option>720p</option><option>1080p</option>{(provider === "seedance" || provider === "kling" && !turbo) && <option>4k</option>}</>}</select></Field><Field label="画幅">{minimax ? <small>{selectedShot?.generation_mode === "image_to_video" ? "由首帧图片决定" : "模型默认横屏；竖屏请用竖版首帧"}</small> : <><select disabled={selectedShot?.generation_mode === "image_to_video" || selectedShot?.generation_mode === "first_last_frame"} value={ratio} onChange={(event) => setRatio(event.target.value)}><option>16:9</option><option>9:16</option><option>1:1</option>{provider === "seedance" && <><option>4:3</option><option>3:4</option></>}</select><small>首尾帧模式以图片画幅为准。</small></>}</Field></div>
          {minimax ? <Field label="时长"><select value={duration} onChange={e => setDuration(Number(e.target.value))}><option value={6}>6 秒</option>{resolution === "768P" && <option value={10}>10 秒</option>}</select><small>1080P 仅支持 6 秒。转场只用其中 1–2 秒也可以。</small></Field> : <Field label={provider === "kling" ? "时长（3–15 秒）" : "时长（4–15 秒）"}><input type="range" min={provider === "kling" ? 3 : 4} max={15} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /><div className="range-value"><strong>{duration}</strong><span>SECONDS</span></div></Field>}
          {provider === "kling" && <><Field label="声音"><select disabled={turbo} value={turbo || nativeAudio ? "native" : "off"} onChange={e => setNativeAudio(e.target.value === "native")}><option value="off">无声画面，后期配音</option><option value="native">原生音画同步</option></select></Field>{!turbo && selectedShot?.generation_mode !== "text_to_video" && <details><summary>高级：额外手动主体</summary><Field label="可灵主体 ID（可选）" hint="本地角色已自动引用，无需重复填写。仅填当前 API 账户可用的主体 ID；官网互通未验证。"><input value={elementId} onChange={e => setElementId(e.target.value)} /></Field><Field label="主体引用名" hint={`请在提示词中使用 @${elementAlias}。`}><input value={elementAlias} onChange={e => setElementAlias(e.target.value)} /></Field></details>}</>}
          {provider === "seedance" && <Field label="声音"><select value={nativeAudio ? "native" : "off"} onChange={e => setNativeAudio(e.target.value === "native")}><option value="native">原生音画同步</option><option value="off">无声画面，后期配音</option></select></Field>}
          <div className="credit-note"><Coins size={18} /><div><strong>{minimax ? "Token Plan · 剩余额度未核验" : provider === "seedance" ? "火山方舟 API 额度" : "可灵 API 额度"}</strong><span>{minimax ? "每日 3 次按你的账号权益确认，与旧工作台共用；不自动切换付费 Key。" : "实际消耗以所选平台账单为准。"}</span></div></div>
          <SubmitButton busy={busy} disabled={revisionLocked || !selectedShot || !ready || !supportedMode} className="generate-button"><Sparkles size={17} /> 提交生成</SubmitButton>
          <small className="button-hint">点击后仍需二次确认，不会静默扣除额度。</small>
        </aside>
      </form>

      {keyframeShot && <ShotKeyframeModal key={keyframeShot.id} shot={keyframeShot} initialRatio={ratio} onClose={() => setKeyframeShot(null)} onSelected={(updated, selected) => {
        setFirstFrame(null); setConfirm(false); setSavedKeyframe(selected);
        setBoard(current => current ? { ...current, scenes: current.scenes.map(scene => ({ ...scene, shots: scene.shots.map(shot => shot.id === updated.id ? updated : shot) })) } : current);
        setKeyframeShot(null);
        notify("首帧已选定并保存，视频生成时可直接使用；尚未提交视频任务。");
      }} />}

      {editingShot && <ShotCharactersModal key={editingShot.id} projectId={project.id} shot={editingShot} onClose={() => setEditingShot(null)} onSaved={updated => {
        setBindingPreview(null);
        setConfirm(false);
        setBoard(current => current ? { ...current, scenes: current.scenes.map(scene => ({ ...scene, shots: scene.shots.map(shot => shot.id === updated.id ? updated : shot) })) } : current);
        setEditingShot(null);
        notify("角色关联已保存；可灵主体将自动重新核对。未提交生成任务。");
      }} />}

      {confirm && selectedShot && <Modal title="确认提交视频任务" eyebrow="GENERATION CONFIRMATION" onClose={() => !busy && setConfirm(false)}><div className="confirm-summary"><div className="confirm-icon"><ShieldAlert size={24} /></div><p>即将通过 {providerLabel} 提交视频生成任务。</p><dl><div><dt>镜头</dt><dd>{selectedShot.id}</dd></div><div><dt>模型</dt><dd>{model || "后端默认模型"}</dd></div><div><dt>规格</dt><dd>{resolution} · {duration}s</dd></div><div><dt>模式</dt><dd>{modeNames[selectedShot.generation_mode]}</dd></div>{(provider === "kling" || provider === "seedance") && <div><dt>声音</dt><dd>{turbo || nativeAudio ? "原生音画同步" : "无声，后期配音"}</dd></div>}</dl><Callout>{minimax ? "生成会使用账号资源；不采用片段不代表退还额度。本平台未核验实时权益，也无法阻止平台侧补充额度扣费。请先检查后台；超时不要直接重复提交。" : "实际扣费以所选平台账单为准。"}</Callout>{minimax && <label className="quota-confirm"><input type="checkbox" checked={quotaConfirmed} onChange={e => setQuotaConfirmed(e.target.checked)} />我已核对订阅 Key、Hailuo 2.3 权益及今日剩余额度，确认提交一次。</label>}<div className="modal-actions"><button className="ghost-button" type="button" disabled={busy} onClick={() => setConfirm(false)}>返回检查</button><button className="danger-confirm" type="button" disabled={busy || minimax && !quotaConfirmed} onClick={() => void submitGeneration()}>{busy ? <LoaderCircle className="spinning" size={16} /> : <Sparkles size={16} />} 确认并提交</button></div></div></Modal>}
    </div>
  );
}

function AssetInput({ label, file, onChange, optional = false }: { label: string; file: File | null; onChange: (file: File | null) => void; optional?: boolean }) {
  return <label className="asset-input"><Upload size={23} /><strong>{label}{optional && <small> 可选</small>}</strong><span>{file?.name || "JPG / PNG / WebP"}</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => onChange(event.target.files?.[0] ?? null)} /></label>;
}
