import { useEffect, useRef, useState } from "react";
import { BookOpen, Check, Plus, Save, Sparkles } from "lucide-react";
import { api, errorMessage, postJson } from "../api";
import { Callout, Empty, Field, Modal } from "../components";
import type { Episode, Project, Shot } from "../types";
import { ExternalStoryboardImport } from "../ExternalStoryboardImport";

type DraftScene = { id?: string | null; location: string; time_of_day: string; summary: string; shots: Partial<Shot>[] };
type Draft = { screenplay: string; scenes: DraftScene[] };
type Script = { id: string; title: string; source_idea: string; version: number; status: string; structured_content: { draft?: Draft; episode_id?: string; screenplay_approved?: boolean; published_episode_id?: string; reopen?: { episode_id: string } } };
const blankShot = (): Partial<Shot> => ({ order_index: 1, duration: 5, shot_type: "中景", visual_description: "", dialogue: "", generation_mode: "image_to_video", characters: [] });

export function StoryPage({ project, preselectedEpisodeId, notify, onPublished }: { project?: Project; preselectedEpisodeId?: string; notify: (message: string, kind?: "success" | "error") => void; onPublished: (episodeId: string) => void }) {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [episodeId, setEpisodeId] = useState("");
  const [scripts, setScripts] = useState<Script[]>([]);
  const [script, setScript] = useState<Script | null>(null);
  const [draft, setDraft] = useState<Draft>({ screenplay: "", scenes: [] });
  const [title, setTitle] = useState("");
  const [synopsis, setSynopsis] = useState("");
  const [duration, setDuration] = useState(60);
  const [style, setStyle] = useState("电影写实");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [approved, setApproved] = useState(false);
  const storyboardPanel = useRef<HTMLElement>(null);
  const [autofilled, setAutofilled] = useState(false);
  const [confirmation, setConfirmation] = useState<"reopen-storyboard" | "cancel-revision" | "generate-storyboard" | null>(null);
  const [status, setStatus] = useState<{ configured: boolean; missing: string[]; model: string }>();
  const published = Boolean(script?.structured_content.published_episode_id);
  const revising = Boolean(script?.structured_content.reopen);
  const visibleScripts = episodeId ? scripts.filter(item => item.structured_content.episode_id === episodeId) : scripts;

  function chooseEpisode(id: string, items = scripts, episodeItems = episodes) {
    if (dirty && !window.confirm("当前编辑未保存，切换分集会丢失修改。继续？")) return;
    setEpisodeId(id);
    const episode = episodeItems.find(item => item.id === id);
    setTitle(episode?.title || project?.name || ""); setSynopsis(episode?.summary || project?.description || "");
    setDuration(episode?.duration_target || 180);
    const candidates = id ? items.filter(item => item.structured_content.episode_id === id) : items;
    const preferred = candidates.find(item => item.id === episode?.script_id) || candidates[0];
    if (preferred) select(preferred);
    else { setScript(null); setDraft({ screenplay: "", scenes: [] }); setApproved(false); setDirty(false); }
  }

  function select(item: Script) {
    setAutofilled(false);
    setScript(item); setDraft(item.structured_content.draft || { screenplay: item.source_idea, scenes: [] });
    setApproved(Boolean(item.structured_content.screenplay_approved)); setDirty(false);
  }
  async function load() {
    if (!project) return;
    const items = await api<Script[]>(`/projects/${project.id}/scripts`);
    setScripts(items);
  }
  useEffect(() => {
    setScript(null); setDraft({ screenplay: "", scenes: [] }); setScripts([]); setDirty(false);
    setTitle(project?.name || ""); setSynopsis(project?.description || "");
    if (project) void Promise.all([
      api<Script[]>(`/projects/${project.id}/scripts`), api<Episode[]>(`/projects/${project.id}/episodes`),
    ]).then(([items, episodeItems]) => {
      setScripts(items); setEpisodes(episodeItems);
      chooseEpisode(episodeItems.find(item => item.id === preselectedEpisodeId)?.id || episodeItems[0]?.id || "", items, episodeItems);
    }).catch(error => notify(errorMessage(error), "error"));
    void api<typeof status>("/planning/status").then(setStatus).catch(error => notify(errorMessage(error), "error"));
  }, [project?.id]);
  function edit(next: Draft, screenplayChanged = false) { setDraft(next); setDirty(true); if (screenplayChanged) setApproved(false); }
  function editScene(index: number, next: DraftScene) { edit({ ...draft, scenes: draft.scenes.map((scene, i) => i === index ? next : scene) }); }
  async function create(ai: boolean) {
    if (!project || busy) return;
    if (!title.trim() || synopsis.trim().length < 5) return notify("请填写标题和至少五个字的故事简介", "error");
    setBusy(true);
    try {
      const episodeQuery = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : "";
      const item = ai
        ? await postJson<Script>(`/projects/${project.id}/story-drafts${episodeQuery}`, { title, synopsis, duration, style, genre: String(project.settings_json.genre || "剧情") })
        : await postJson<Script>(`/projects/${project.id}/scripts${episodeQuery}`, { title, source_idea: synopsis, episode_duration: duration,
          structured_content: { draft: { screenplay: synopsis, scenes: [{ location: "待填写场景", time_of_day: "", summary: "", shots: [blankShot()] }] }, screenplay_approved: false } });
      select(item); await load(); notify(ai ? "剧本和分镜草稿已生成，请审阅" : "手动草稿已建立，请填写剧本与镜头");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function save(approve = approved) {
    if (!script) return;
    setBusy(true);
    try {
      const saved = await api<Script>(`/scripts/${script.id}/review`, { method: "PUT", body: JSON.stringify({ expected_version: script.version, draft, screenplay_approved: approve }) });
      select(saved); await load(); notify(approve ? "剧本已确认，分镜可继续审阅" : "草稿已保存");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function publish() {
    if (!script || dirty || !approved) return;
    setBusy(true);
    try {
      const result = await postJson<{episode_id: string}>(`/scripts/${script.id}/publish-storyboard`, { expected_version: script.version });
      notify("已批准分镜并进入本集制作，可逐镜头选择平台生成"); onPublished(result.episode_id);
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function confirmAction() {
    if (!script || !confirmation || busy) return;
    const action = confirmation;
    setBusy(true);
    try {
      const saved = await postJson<Script>(`/scripts/${script.id}/${action}`, {
        expected_version: script.version, confirmed: true,
        ...(action === "generate-storyboard" ? { screenplay: draft.screenplay } : {}),
      });
      select(saved); await load(); setConfirmation(null);
      if (action === "generate-storyboard") {
        setAutofilled(true);
        storyboardPanel.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      notify(action === "generate-storyboard" ? "分镜审阅稿已生成，剧本原文已保留；请审阅后再批准" : action === "reopen-storyboard" ? "已撤回修订，原镜头与素材已保留" : "已恢复发布状态，原镜头与素材未改动");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  if (!project) return <Empty icon={<BookOpen />} title="先选择一个项目" copy="故事、剧本和分镜会保存在该项目内。" />;
  return <div className="story-workspace page-stack">
    <ExternalStoryboardImport key={project.id} projectId={project.id} episodeId={episodeId} disabled={busy || dirty} notify={notify} onImported={async id => {
      const imported = await api<Script>(`/scripts/${id}`);
      select(imported); await load();
      setEpisodes(await api<Episode[]>(`/projects/${project.id}/episodes`));
    }} />
    <section className="panel"><Field label={`${project.name} · 选择分集`} hint="每集保留独立版本；默认打开本集当前关联稿。"><select disabled={busy} value={episodeId} onChange={e => chooseEpisode(e.target.value)}><option value="">全部审阅稿 / 未指定分集</option>{episodes.map(episode => <option key={episode.id} value={episode.id}>EP {String(episode.episode_number).padStart(2, "0")} · {episode.title}</option>)}</select></Field></section>
    <div className="workflow-steps"><span className="active">01 故事简介</span><span className={script ? "active" : ""}>02 剧本审阅</span><span className={approved ? "active" : ""}>03 分镜审阅</span><span>04 逐镜头生成</span></div>
    <div className="split-layout">
      <section className="panel">
        <h3>输入故事简介</h3>
        <Field label="标题"><input value={title} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label="故事简介"><textarea rows={6} value={synopsis} onChange={e => setSynopsis(e.target.value)} placeholder="人物是谁？想达成什么？阻碍、转折和结尾是什么？" /></Field>
        <div className="field-grid two"><Field label="目标时长（秒）"><input type="number" min={15} max={600} value={duration} onChange={e => setDuration(Number(e.target.value))} /></Field><Field label="视觉风格"><input value={style} onChange={e => setStyle(e.target.value)} /></Field></div>
        <div className="page-actions"><button className="primary-button" disabled={busy || !status?.configured} onClick={() => void create(true)}><Sparkles size={16} />{busy ? "处理中…" : "生成剧本和分镜草稿"}</button><button className="soft-button" disabled={busy} onClick={() => void create(false)}><Plus size={16} /> 手动建立草稿</button></div>
        {!status?.configured && <Callout>自动创作需要配置文本模型。后端环境变量：PLANNING_API_URL、PLANNING_API_KEY、PLANNING_MODEL。手动草稿可直接使用。</Callout>}
        {status?.configured && <p className="muted">文本模型：{status.model}。生成草稿会调用该模型。</p>}
      </section>
      <aside className="panel"><h3>{episodeId ? "本集审阅稿" : "全部审阅稿"}</h3>{visibleScripts.length ? visibleScripts.map(item => <button className={`draft-entry ${script?.id === item.id ? "selected" : ""}`} key={item.id} disabled={busy} onClick={() => {
        if (dirty && !window.confirm("当前编辑未保存，切换会丢失这些修改。继续？")) return;
        select(item);
      }}><strong>{item.title}</strong><span>修订 {item.version} · {item.structured_content.published_episode_id ? "已发布" : item.structured_content.screenplay_approved ? "剧本已确认" : "待审阅"}</span></button>) : <p className="muted">还没有审阅稿。</p>}</aside>
    </div>
    {script && <>
      <section className="panel"><div className="panel-heading"><h3>剧本审阅 · {script.title}</h3><span>{published ? "已发布" : dirty ? "未保存修改" : `修订 ${script.version}`}</span></div>
        {published && <div className="page-actions"><button className="soft-button" disabled={busy} onClick={() => setConfirmation("reopen-storyboard")}>撤回分镜，重新编辑</button></div>}
        {revising && <Callout tone="warning">本集正在修订，暂不能提交新视频或首帧任务。原镜头和历史素材保留；批准后按原 ID 更新，移除的镜头归档。<button className="text-button" disabled={busy} onClick={() => setConfirmation("cancel-revision")}>取消本次修订，恢复发布状态</button></Callout>}
        <Field label="完整剧本" hint="可直接粘贴其他模型修改后的完整剧本。根据当前剧本生成分镜不会改写这里的原文。"><textarea className="screenplay-editor" rows={14} disabled={published || busy} value={draft.screenplay} onChange={e => edit({ ...draft, screenplay: e.target.value }, true)} /></Field>
        {!published && <><div className="page-actions"><button className="primary-button" disabled={busy || !status?.configured || draft.screenplay.trim().length < 5} onClick={() => setConfirmation("generate-storyboard")}><Sparkles size={16} />{busy && confirmation === "generate-storyboard" ? "正在拆分分镜，请勿重复提交…" : "根据当前剧本生成分镜"}</button></div><p className="muted">直接使用文本框中的最新剧本，无需先保存；成功后替换当前待审分镜并保存。不会自动发布或生成视频。</p>{!status?.configured && <Callout>尚未配置文本模型，暂不能自动拆分分镜。请配置后端 PLANNING_API_URL、PLANNING_API_KEY、PLANNING_MODEL 并重启；现在仍可粘贴、保存和手动编辑。</Callout>}</>}
        {!published && <div className="page-actions"><button className="soft-button" disabled={busy} onClick={() => void save(false)}><Save size={16} /> 保存待审稿</button><button className="primary-button" disabled={busy || !draft.screenplay.trim()} onClick={() => void save(true)}><Check size={16} /> 确认剧本并保存</button></div>}
      </section>
      <section className="panel" ref={storyboardPanel}><div className="panel-heading"><h3>分镜审阅</h3><span>{draft.scenes.reduce((sum, scene) => sum + scene.shots.length, 0)} 镜头 · {draft.scenes.reduce((sum, scene) => sum + scene.shots.reduce((n, shot) => n + Number(shot.duration || 0), 0), 0)} 秒</span></div>
        {autofilled && <Callout tone="success">分镜已自动填入并保存为待审稿。下方画面、对白、时长、运镜和视频提示词均可继续微调；改完请保存。尚未批准，也未生成视频。</Callout>}
        {draft.scenes.map((scene, sceneIndex) => <fieldset className="review-scene" key={scene.id || sceneIndex} disabled={published || busy}><legend>场景 {sceneIndex + 1}</legend>
          <button className="text-button" onClick={() => edit({ ...draft, scenes: draft.scenes.filter((_, i) => i !== sceneIndex) })}>移除此场景</button>
          <div className="field-grid two"><Field label="地点"><input value={scene.location} onChange={e => editScene(sceneIndex, { ...scene, location: e.target.value })} /></Field><Field label="时间"><input value={scene.time_of_day} onChange={e => editScene(sceneIndex, { ...scene, time_of_day: e.target.value })} /></Field></div>
          <Field label="场景概要"><input value={scene.summary} onChange={e => editScene(sceneIndex, { ...scene, summary: e.target.value })} /></Field>
          {scene.shots.map((shot, shotIndex) => {
            const change = (data: Partial<Shot>) => editScene(sceneIndex, { ...scene, shots: scene.shots.map((s, i) => i === shotIndex ? { ...s, ...data } : s) });
            return <div className="review-shot" key={shot.id || shotIndex}><strong>镜头 {shotIndex + 1}</strong>
              <div className="field-grid three"><Field label="时长"><input type="number" min={3} max={30} value={shot.duration} onChange={e => change({ duration: Number(e.target.value) })} /></Field><Field label="景别"><input value={shot.shot_type} onChange={e => change({ shot_type: e.target.value })} /></Field><Field label="生成方式"><select value={shot.generation_mode} onChange={e => change({ generation_mode: e.target.value as Shot["generation_mode"] })}><option value="image_to_video">首帧生视频</option><option value="text_to_video">文生视频</option><option value="first_last_frame">首尾帧</option><option value="reference_to_video">多模态参考（Seedance）</option></select></Field></div>
              <Field label="画面与动作"><textarea rows={3} value={shot.visual_description} onChange={e => change({ visual_description: e.target.value })} /></Field>
              <Field label="对白 / 旁白"><input value={shot.dialogue || ""} onChange={e => change({ dialogue: e.target.value })} /></Field>
              <Field label="视频生成提示词" hint="修改画面或对白后，请同步检查此处的台词、说话人、口型和声音指令。画面编辑不会覆盖这份提示词。"><textarea rows={5} value={shot.generation_prompt || ""} onChange={e => change({ generation_prompt: e.target.value })} /></Field>
              <Field label="运镜"><input value={shot.camera_movement || "static"} onChange={e => change({ camera_movement: e.target.value })} /></Field>
              <button className="text-button" onClick={() => editScene(sceneIndex, { ...scene, shots: scene.shots.filter((_, i) => i !== shotIndex) })}>移除此镜头</button>
            </div>;
          })}
          <button className="soft-button" onClick={() => editScene(sceneIndex, { ...scene, shots: [...scene.shots, blankShot()] })}><Plus size={14} /> 添加镜头</button>
        </fieldset>)}
        {!published && <div className="page-actions"><button className="soft-button" disabled={busy} onClick={() => edit({ ...draft, scenes: [...draft.scenes, { location: "", time_of_day: "", summary: "", shots: [blankShot()] }] })}>添加场景</button><button className="soft-button" disabled={busy} onClick={() => void save()}><Save size={16} /> 保存分镜</button><button className="primary-button" disabled={busy || dirty || !approved} onClick={() => void publish()}><Check size={16} /> {revising ? "批准修订，更新原分镜" : "批准分镜，进入制作"}</button></div>}
        <p className="muted">修改剧本会撤销剧本确认。批准分镜后才将镜头写入当前分集，不会重复新建一集；后续视频生成仍需逐镜头提交。</p>
      </section>
    </>}
    {confirmation && <Modal title={confirmation === "generate-storyboard" ? "根据当前剧本生成分镜" : confirmation === "reopen-storyboard" ? "撤回分镜，重新编辑" : "取消本次修订"} onClose={() => !busy && setConfirmation(null)}>
      <Callout tone="warning">{confirmation === "generate-storyboard" ? `将完整剧本及本项目角色资料发送给文本模型 ${status?.model || ""}，可能产生费用。成功后替换当前待审分镜（包括未保存的分镜修改），但保留剧本原文；不会发布或提交视频任务。失败时保留当前内容。` : confirmation === "reopen-storyboard" ? "将已发布分镜转为可编辑审阅稿。本集暂停新的制作提交，已有生成任务不取消、不退款，原镜头、首帧和视频保留。重新批准后，修改过的素材需复核。" : "将恢复撤回前的发布状态。已保存修订稿保留在历史中，未保存编辑将丢弃；原制作镜头和素材不变。"}</Callout>
      <div className="modal-actions"><button className="ghost-button" disabled={busy} onClick={() => setConfirmation(null)}>返回</button><button className="primary-button" disabled={busy} onClick={() => void confirmAction()}>{busy ? "处理中…" : confirmation === "generate-storyboard" ? "确认并生成分镜审阅稿" : "确认"}</button></div>
    </Modal>}
  </div>;
}
