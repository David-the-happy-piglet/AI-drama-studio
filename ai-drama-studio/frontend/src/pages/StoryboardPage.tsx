import { ArrowRight, Camera, Clapperboard, Clock3, MapPin, Plus, Rows3, Users } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, errorMessage, mediaUrl, postJson } from "../api";
import { Callout, Empty, Field, Modal, SubmitButton } from "../components";
import type { KeyframeStudio } from "../keyframes";
import type { Character, Episode, Project, Scene, Shot, Storyboard } from "../types";
import { BatchProductionPanel } from "../BatchProductionPanel";
import { SceneMasterBinding, type SceneAsset } from "./SceneAssetsPage";

type SourceShot = { source_shot_id: string; scene_heading: string; shot_heading: string; fields: Record<string, string> };
type SourceDetails = { revision: string; shots: Record<string, SourceShot> };

export function StoryboardPage({
  project,
  notify,
  onGenerate,
  onReview,
  preselectedEpisodeId,
}: {
  project?: Project;
  notify: (message: string, kind?: "success" | "error") => void;
  onGenerate: (shotId: string, episodeId: string) => void;
  onReview: (episodeId: string) => void;
  preselectedEpisodeId?: string;
}) {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [selectedEpisodeId, setSelectedEpisodeId] = useState("");
  const [storyboard, setStoryboard] = useState<Storyboard | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [sceneAssets, setSceneAssets] = useState<SceneAsset[]>([]);
  const [episodeModal, setEpisodeModal] = useState(false);
  const [sceneModal, setSceneModal] = useState(false);
  const [shotScene, setShotScene] = useState<Scene | null>(null);
  const [detailShot, setDetailShot] = useState<Shot | null>(null);
  const [sourceDetails, setSourceDetails] = useState<SourceDetails | null>(null);
  const revisionLocked = storyboard?.status === "revising";

  async function loadEpisodes(preferred?: string) {
    if (!project) { setEpisodes([]); setSelectedEpisodeId(""); return; }
    try {
      const [episodeData, characterData, assetData] = await Promise.all([
        api<Episode[]>(`/projects/${project.id}/episodes`),
        api<Character[]>(`/projects/${project.id}/characters`),
        api<SceneAsset[]>(`/projects/${project.id}/scene-assets`),
      ]);
      setEpisodes(episodeData); setCharacters(characterData);
      setSceneAssets(assetData);
      setSelectedEpisodeId((current) => preferred || (episodeData.some((item) => item.id === current) ? current : episodeData.find(item => item.id === preselectedEpisodeId)?.id || episodeData[0]?.id || ""));
    } catch (error) { notify(errorMessage(error), "error"); }
  }

  async function loadStoryboard() {
    if (!selectedEpisodeId) return setStoryboard(null);
    try { setStoryboard(await api<Storyboard>(`/episodes/${selectedEpisodeId}/storyboard`)); }
    catch (error) { notify(errorMessage(error), "error"); }
  }

  useEffect(() => { void loadEpisodes(); }, [project?.id]);
  useEffect(() => { void loadStoryboard(); }, [selectedEpisodeId]);
  useEffect(() => {
    setSourceDetails(null);
    if (!project) return;
    let active = true;
    void fetch(`/media/projects/${encodeURIComponent(project.id)}/storyboard_details.json`, { cache: "no-store" })
      .then(response => response.ok ? response.json() as Promise<SourceDetails> : null)
      .then(details => { if (active) setSourceDetails(details); })
      .catch(() => { /* Projects without an imported source use the saved shot fields. */ });
    return () => { active = false; };
  }, [project?.id]);

  if (!project) return <Empty icon={<Rows3 size={32} />} title="请先选择项目" copy="分镜需要归属于一部短剧项目。" />;

  return (
    <div className="page-stack">
      <section className="page-intro compact">
        <div><span className="eyebrow">SHOT BOARD / {project.name}</span><h2>分镜台</h2><p>先锁定叙事与镜头语言，再按镜头独立生成和重试。</p></div>
        <div className="page-actions"><button className="soft-button" onClick={() => setEpisodeModal(true)}><Plus size={15} /> 新建分集</button>{selectedEpisodeId && <><button className="soft-button" onClick={() => onReview(selectedEpisodeId)}>审阅 / 修改本集</button><button className="primary-button" disabled={revisionLocked} onClick={() => setSceneModal(true)}><Plus size={15} /> 添加场景</button></>}</div>
      </section>
      {revisionLocked && <Callout tone="warning">本集分镜已撤回修订，下方保留原制作分镜供查看。重新批准后才能继续制作。<button className="text-button" onClick={() => onReview(selectedEpisodeId)}>继续编辑审阅稿</button></Callout>}

      {episodes.length ? (
        <>
          <div className="episode-tabs" role="tablist">
            {episodes.map((episode) => <button key={episode.id} className={episode.id === selectedEpisodeId ? "active" : ""} onClick={() => setSelectedEpisodeId(episode.id)}><span>EP {String(episode.episode_number).padStart(2, "0")}</span><strong>{episode.title}</strong></button>)}
          </div>
          <section className="board-header">
            <div><span className="eyebrow">CURRENT EPISODE</span><h3>{storyboard?.title || "读取中…"}</h3><p>{storyboard?.summary || "尚未填写本集概要。"}</p></div>
            <div className="board-stat"><Clock3 size={16} /><strong>{storyboard?.duration_target || 0}s</strong><span>目标时长</span></div>
            <div className="board-stat"><Rows3 size={16} /><strong>{storyboard?.scenes.length || 0}</strong><span>场景</span></div>
            <div className="board-stat"><Clapperboard size={16} /><strong>{storyboard?.scenes.reduce((sum, scene) => sum + scene.shots.length, 0) || 0}</strong><span>镜头</span></div>
          </section>

          {storyboard && <BatchProductionPanel key={storyboard.id} board={storyboard} notify={notify} onChanged={() => void loadStoryboard()} onShot={id => onGenerate(id, selectedEpisodeId)} />}
          <div className="scene-list">
            {storyboard?.scenes.length ? storyboard.scenes.map((scene) => (
              <article className="scene-block" key={scene.id}>
                <header className="scene-heading">
                  <div className="scene-number">SC<br /><strong>{String(scene.order_index).padStart(2, "0")}</strong></div>
                  <div className="scene-title"><span><MapPin size={13} /> {scene.location_description || "未定地点"} · {scene.time_of_day || "未定时间"}</span><h3>{scene.summary || "未填写场景概要"}</h3></div>
                  <button className="soft-button" disabled={revisionLocked} onClick={() => setShotScene(scene)}><Plus size={14} /> 添加镜头</button>
                </header>
                <SceneMasterBinding sceneId={scene.id} sceneVersion={scene.version} assets={sceneAssets} locked={Boolean(revisionLocked)} notify={notify} onSaved={() => void loadStoryboard()} />
                <div className="shot-strip">
                  {scene.shots.length ? scene.shots.map((shot) => (
                    <article className="shot-card" key={shot.id}>
                      <div className="shot-preview"><Camera size={25} /><span>{shot.generation_mode.replaceAll("_", " ")}</span></div>
                      <div className="shot-copy"><small>{sourceDetails?.shots[shot.id]?.source_shot_id || `SH ${String(shot.order_index).padStart(2, "0")}`} · {shot.duration}s</small><strong>{shot.shot_type}</strong><p>{shot.visual_description}</p><div className="shot-actions"><button className="soft-button" onClick={() => setDetailShot(shot)}>查看详情</button><button className="text-button" disabled={revisionLocked} onClick={() => onGenerate(shot.id, selectedEpisodeId)}>去生成 <ArrowRight size={12} /></button></div></div>
                    </article>
                  )) : <button className="add-shot-card" disabled={revisionLocked} onClick={() => setShotScene(scene)}><Plus size={20} /><span>设计第一个镜头</span></button>}
                </div>
              </article>
            )) : episodes.find(item => item.id === selectedEpisodeId)?.script_id ? <Empty icon={<Rows3 size={30} />} title="本集分镜仍在待审稿中" copy="剧本和分镜草稿没有丢失。请先打开本集审阅稿，确认后再进入镜头制作。" action={<button className="primary-button" onClick={() => onReview(selectedEpisodeId)}>打开本集审阅稿</button>} /> : <Empty icon={<Rows3 size={30} />} title="本集还没有场景" copy="添加一个场景，再把它拆成可生成的镜头。" action={<button className="primary-button" onClick={() => setSceneModal(true)}><Plus size={15} /> 添加场景</button>} />}
          </div>
        </>
      ) : <Empty icon={<Clapperboard size={32} />} title="先创建第一集" copy="定义本集时长和故事目标后，就可以开始拆场景。" action={<button className="primary-button" onClick={() => setEpisodeModal(true)}><Plus size={15} /> 新建分集</button>} />}

      {episodeModal && <EpisodeModal project={project} nextNumber={episodes.length + 1} onClose={() => setEpisodeModal(false)} notify={notify} onCreated={(episode) => { setEpisodeModal(false); void loadEpisodes(episode.id); }} />}
      {sceneModal && selectedEpisodeId && <SceneModal episodeId={selectedEpisodeId} nextIndex={(storyboard?.scenes.length || 0) + 1} characters={characters} onClose={() => setSceneModal(false)} notify={notify} onCreated={() => { setSceneModal(false); void loadStoryboard(); }} />}
      {shotScene && <ShotModal scene={shotScene} nextIndex={(storyboard?.scenes.find((item) => item.id === shotScene.id)?.shots.length || 0) + 1} characters={characters} onClose={() => setShotScene(null)} notify={notify} onCreated={() => { setShotScene(null); void loadStoryboard(); }} />}
      {detailShot && <ShotDetailsModal shot={detailShot} source={sourceDetails?.shots[detailShot.id]} characters={characters} onClose={() => setDetailShot(null)} onGenerate={() => { setDetailShot(null); onGenerate(detailShot.id, selectedEpisodeId); }} />}
    </div>
  );
}

function ShotDetailsModal({ shot, source, characters, onClose, onGenerate }: { shot: Shot; source?: SourceShot; characters: Character[]; onClose: () => void; onGenerate: () => void }) {
  const [frame, setFrame] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void api<KeyframeStudio>(`/shots/${shot.id}/keyframe-studio`).then(data => {
      if (active && data.selected?.usable) setFrame(mediaUrl(data.selected.path));
    }).catch(() => {});
    return () => { active = false; };
  }, [shot.id]);
  const names = shot.characters.map(item => characters.find(character => character.id === item.character_id)?.name || item.character_id).join("、");
  const fields = source?.fields;
  const rows: [string, string | undefined][] = fields ? [
    ["人物 / 服装 / 场景", fields["人物/服装/场景"]], ["镜头 / 构图", fields["镜头"]], ["首帧", fields["首帧"]],
    ["动作与表情", fields["动作与表情"]], ["对白 / 旁白", fields["对白"] || fields["对白（画外音）"] || fields["对白（画外音，接上）"]],
    ["环境音", fields["环境音"]], ["连续性", fields["连续性"]], ["表情提示词", fields["表情提示词"]],
    ["可灵提示词", fields["可灵提示词"]], ["Seedance 提示词", fields["Seedance提示词"]], ["原生音频", fields["原生音频"]],
  ] : [
    ["出场人物", names], ["镜头 / 构图", shot.composition], ["首帧", shot.visual_description], ["动作", shot.action],
    ["表情", shot.emotion], ["对白", shot.dialogue], ["连续性", shot.continuity_requirements.join("；")],
    ["视频提示词", shot.generation_prompt || ""],
  ];
  return <Modal title={`${source?.source_shot_id || `SH ${shot.order_index}`} · 分镜详情`} eyebrow={source?.scene_heading || "SHOT DETAILS"} onClose={onClose} wide>
    <div className="shot-detail">
      <p className="muted">{source?.shot_heading || `${shot.shot_type} · ${shot.duration} 秒`} · 已存镜头 {shot.id}</p>
      {frame && <img className="shot-detail-frame" src={frame} alt={`${source?.source_shot_id || shot.id} 已审核首帧`} />}
      {rows.filter(([, value]) => Boolean(value)).map(([label, value]) => <div className="shot-detail-row" key={label}><strong>{label}</strong><p>{value}</p></div>)}
      {source && <Callout>上方为最新版分镜文档的描述；已审核首帧和平台现有画面资产保持原样。提交视频时使用平台当前保存的提示词。</Callout>}
      <div className="modal-actions"><button className="soft-button" onClick={onClose}>关闭</button><button className="primary-button" onClick={onGenerate}>前往生成</button></div>
    </div>
  </Modal>;
}

function EpisodeModal({ project, nextNumber, onClose, onCreated, notify }: { project: Project; nextNumber: number; onClose: () => void; onCreated: (episode: Episode) => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [title, setTitle] = useState(""); const [summary, setSummary] = useState(""); const [duration, setDuration] = useState(90); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { const item = await postJson<Episode>(`/projects/${project.id}/episodes`, { episode_number: nextNumber, title, duration_target: duration, summary }); notify("剧集已创建"); onCreated(item); } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); } }
  return <Modal title="新建分集" eyebrow="EPISODE" onClose={onClose}><form className="modal-form" onSubmit={(event) => void submit(event)}><Field label="标题"><input required value={title} onChange={(event) => setTitle(event.target.value)} placeholder={`第 ${nextNumber} 集标题`} /></Field><Field label="目标时长（秒）"><input type="number" min={1} max={7200} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /></Field><Field label="本集概要"><textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={4} placeholder="本集发生什么，结尾留下什么钩子？" /></Field><div className="modal-actions"><button className="ghost-button" type="button" onClick={onClose}>取消</button><SubmitButton busy={busy}>创建第 {nextNumber} 集</SubmitButton></div></form></Modal>;
}

function SceneModal({ episodeId, nextIndex, characters, onClose, onCreated, notify }: { episodeId: string; nextIndex: number; characters: Character[]; onClose: () => void; onCreated: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [location, setLocation] = useState(""); const [time, setTime] = useState("夜晚"); const [summary, setSummary] = useState(""); const [characterIds, setCharacterIds] = useState<string[]>([]); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { await postJson<Scene>(`/episodes/${episodeId}/scenes`, { order_index: nextIndex, location_description: location, time_of_day: time, summary, character_ids: characterIds }); notify("场景已添加"); onCreated(); } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); } }
  return <Modal title={`场景 ${String(nextIndex).padStart(2, "0")}`} eyebrow="SCENE DESIGN" onClose={onClose}><form className="modal-form" onSubmit={(event) => void submit(event)}><div className="field-grid two"><Field label="地点"><input required value={location} onChange={(event) => setLocation(event.target.value)} placeholder="旧城区公寓走廊" /></Field><Field label="时间"><input value={time} onChange={(event) => setTime(event.target.value)} placeholder="雨夜" /></Field></div><Field label="场景目标"><textarea required value={summary} onChange={(event) => setSummary(event.target.value)} rows={4} placeholder="人物在这里要完成什么叙事动作？" /></Field>{characters.length > 0 && <Field label="出场角色"><div className="choice-grid">{characters.map((character) => <label className={characterIds.includes(character.id) ? "choice active" : "choice"} key={character.id}><input type="checkbox" checked={characterIds.includes(character.id)} onChange={(event) => setCharacterIds((ids) => event.target.checked ? [...ids, character.id] : ids.filter((id) => id !== character.id))} /><Users size={14} />{character.name}</label>)}</div></Field>}<div className="modal-actions"><button className="ghost-button" type="button" onClick={onClose}>取消</button><SubmitButton busy={busy}>保存场景</SubmitButton></div></form></Modal>;
}

function ShotModal({ scene, nextIndex, characters, onClose, onCreated, notify }: { scene: Scene; nextIndex: number; characters: Character[]; onClose: () => void; onCreated: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [shotType, setShotType] = useState("中近景"); const [duration, setDuration] = useState(5); const [cameraMovement, setCameraMovement] = useState("static"); const [mode, setMode] = useState("image_to_video"); const [visual, setVisual] = useState(""); const [action, setAction] = useState(""); const [dialogue, setDialogue] = useState(""); const [characterIds, setCharacterIds] = useState<string[]>(scene.character_ids); const [busy, setBusy] = useState(false);
  const allowedCharacters = useMemo(() => characters.filter((character) => !scene.character_ids.length || scene.character_ids.includes(character.id)), [characters, scene.character_ids]);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); try { await postJson<Shot>(`/scenes/${scene.id}/shots`, { order_index: nextIndex, duration, characters: characterIds.map((character_id) => ({ character_id })), shot_type: shotType, camera_angle: "eye_level", camera_movement: cameraMovement, visual_description: visual, action, dialogue, generation_prompt: visual, generation_mode: mode, manual_provider_override: "seedance" }); notify("镜头已保存，可以去生成了"); onCreated(); } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); } }
  return <Modal title={`镜头 ${String(nextIndex).padStart(2, "0")}`} eyebrow="SHOT DESIGN" onClose={onClose} wide><form className="modal-form" onSubmit={(event) => void submit(event)}><div className="field-grid three"><Field label="景别"><select value={shotType} onChange={(event) => setShotType(event.target.value)}><option>特写</option><option>中近景</option><option>中景</option><option>全景</option><option>远景</option></select></Field><Field label="时长"><input type="number" min={4} max={30} value={duration} onChange={(event) => setDuration(Number(event.target.value))} /></Field><Field label="运镜"><select value={cameraMovement} onChange={(event) => setCameraMovement(event.target.value)}><option value="static">固定</option><option value="push_in">缓慢推进</option><option value="pull_out">拉远</option><option value="pan">横摇</option><option value="handheld">手持跟随</option></select></Field></div><Field label="生成方式"><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="image_to_video">首帧生视频</option><option value="text_to_video">文生视频</option><option value="first_last_frame">首尾帧生视频</option><option value="reference_to_video">多模态参考生视频</option></select></Field><Field label="画面描述"><textarea required value={visual} onChange={(event) => setVisual(event.target.value)} rows={5} placeholder="主体、环境、光线、构图与质感；避免只写抽象情绪。" /></Field><div className="field-grid two"><Field label="动作"><input value={action} onChange={(event) => setAction(event.target.value)} placeholder="缓慢转头，目光越过镜头" /></Field><Field label="台词"><input value={dialogue} onChange={(event) => setDialogue(event.target.value)} placeholder="可留空" /></Field></div>{allowedCharacters.length > 0 && <Field label="镜内角色"><div className="choice-grid">{allowedCharacters.map((character) => <label className={characterIds.includes(character.id) ? "choice active" : "choice"} key={character.id}><input type="checkbox" checked={characterIds.includes(character.id)} onChange={(event) => setCharacterIds((ids) => event.target.checked ? [...ids, character.id] : ids.filter((id) => id !== character.id))} />{character.name}</label>)}</div></Field>}<div className="modal-actions"><button className="ghost-button" type="button" onClick={onClose}>取消</button><SubmitButton busy={busy}>保存镜头</SubmitButton></div></form></Modal>;
}
