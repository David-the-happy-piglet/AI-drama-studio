import { useEffect, useState } from "react";
import { api, errorMessage, mediaUrl, postJson, uploadAsset } from "../api";
import { Callout, Field, Modal } from "../components";
import type { Project } from "../types";

export type SceneAsset = { id: string; project_id: string; name: string; description: string; master_reference_path: string; additional_reference_paths: string[]; version: number; bound_scene_ids: string[] };
type Notify = (message: string, kind?: "success" | "error") => void;

export function SceneAssetsPage({ project, notify }: { project?: Project; notify: Notify }) {
  const [assets, setAssets] = useState<SceneAsset[]>([]);
  const [editing, setEditing] = useState<SceneAsset | "new" | null>(null);
  async function load() {
    if (!project) return;
    try { setAssets(await api<SceneAsset[]>(`/projects/${project.id}/scene-assets`)); }
    catch (error) { notify(errorMessage(error), "error"); }
  }
  useEffect(() => { void load(); }, [project?.id]);
  if (!project) return <Callout>请先选择项目，再创建场景母版。</Callout>;
  return <div className="page-stack">
    <section className="page-intro compact"><div><span className="eyebrow">SHARED LOCATION REFERENCES</span><h2>场景母版</h2><p>同一地点的多个场景、多个分集共用参考。固定空间布局、家具和光线，再改变机位。</p></div><button className="primary-button" onClick={() => setEditing("new")}>新建场景母版</button></section>
    <Callout>先上传空景主图，可补充不同机位的参考图。保存仅在本地管理，不会调用可灵；到「分镜」为每个场景关联母版后，生成首帧时会自动携带这些图片，批量首帧和完整工作流同样适用。视频请用「首帧生视频」继承场景；纯文生视频不会使用场景图，也不代表三维空间严格锁定。</Callout>
    {!assets.length && <p>还没有场景母版。可先准备“沈青家 · 夜晚”的房间全景，再在分镜台为同地点场景共用它。</p>}
    {assets.map(asset => <section className="panel batch-production" key={asset.id}><div className="panel-heading"><h3>{asset.name} · V{asset.version}</h3><button className="soft-button" onClick={() => setEditing(asset)}>编辑母版</button></div><p>{asset.description || "未填写额外空间约束"}</p><div className="scene-master-images">{[asset.master_reference_path, ...asset.additional_reference_paths].map((path, i) => <figure key={path}><img src={mediaUrl(path) || ""} alt={`${asset.name} ${i === 0 ? "主视角" : `补充视角 ${i}`}`} /><figcaption>{i === 0 ? "主视角" : `补充视角 ${i}`}</figcaption></figure>)}</div><small>已关联 {asset.bound_scene_ids.length} 个场景；更新图片或设定会使这些场景的旧首帧失效，旧素材仍保留。</small></section>)}
    {editing && <AssetEditor key={typeof editing === "string" ? "new" : editing.id} asset={editing === "new" ? null : editing} project={project} notify={notify} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); void load(); }} />}
  </div>;
}

function AssetEditor({ asset, project, notify, onClose, onSaved }: { asset: SceneAsset | null; project: Project; notify: Notify; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(asset?.name || "");
  const [description, setDescription] = useState(asset?.description || "");
  const [master, setMaster] = useState(asset?.master_reference_path || "");
  const [extra, setExtra] = useState<string[]>(asset?.additional_reference_paths || []);
  const [busy, setBusy] = useState(false);
  async function upload(file: File | undefined, additional: boolean) {
    if (!file) return;
    setBusy(true);
    try {
      const saved = await uploadAsset(project.id, "scene_reference", file);
      if (additional) setExtra(paths => [...paths, saved.path].slice(0,2)); else setMaster(saved.path);
      notify("图片已上传本地，请保存母版后再关联场景");
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  async function save() {
    setBusy(true);
    const body = { name: name.trim(), description, master_reference_path: master, additional_reference_paths: extra };
    try {
      if (asset) await api(`/scene-assets/${asset.id}`, { method: "PUT", body: JSON.stringify({ ...body, expected_version: asset.version }) });
      else await postJson(`/projects/${project.id}/scene-assets`, body);
      notify("场景母版已保存；到分镜台关联后生效"); onSaved();
    } catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  return <Modal title={asset ? "编辑场景母版" : "新建场景母版"} onClose={() => !busy && onClose()} wide>
    <Field label="名称"><input disabled={busy} maxLength={200} value={name} onChange={e => setName(e.target.value)} placeholder="沈青家 · 夜晚" /></Field>
    <Field label="固定场景设定" hint="描述家具与门窗的相对位置、材质和光线。无需重复镜头动作。"><textarea disabled={busy} maxLength={1200} rows={4} value={description} onChange={e => setDescription(e.target.value)} placeholder="书桌靠窗，空椅子在书桌右侧，台灯照向桌面。保持同一房间布局。" /></Field>
    <Field label="主视角母版图" hint="JPG/PNG，每张 ≤10 MB，两边至少 300px，宽高比 2:5–5:2。推荐无人物的房间全景。"><input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={e => { void upload(e.target.files?.[0], false); e.target.value = ""; }} /></Field>
    {master && <img className="scene-master-preview" src={mediaUrl(master) || ""} alt="主视角待保存预览" />}
    <Field label={`补充视角（${extra.length}/2）`} hint="提供同一空间的侧面或反向机位，不要混入另一个房间。"><input type="file" accept="image/png,image/jpeg" disabled={busy || extra.length >= 2} onChange={e => { void upload(e.target.files?.[0], true); e.target.value = ""; }} /></Field>
    <div className="scene-master-images">{extra.map((path, i) => <figure key={path}><img src={mediaUrl(path) || ""} alt={`补充视角 ${i+1}`} /><button className="text-button" disabled={busy} onClick={() => setExtra(paths => paths.filter(p => p !== path))}>移除该参考</button></figure>)}</div>
    {!!asset?.bound_scene_ids.length && <Callout tone="warning">这份母版被 {asset.bound_scene_ids.length} 个场景共用。保存修改后，相关旧首帧将失效；排队中的生成可能被拦截，历史素材不会删除。</Callout>}
    <div className="modal-actions"><button className="ghost-button" disabled={busy} onClick={onClose}>取消</button><button className="primary-button" disabled={busy || !name.trim() || !master} onClick={() => void save()}>{busy ? "处理中…" : "保存母版"}</button></div>
  </Modal>;
}

export function SceneMasterBinding({ sceneId, sceneVersion, assets, locked, notify, onSaved }: { sceneId: string; sceneVersion: number; assets: SceneAsset[]; locked: boolean; notify: Notify; onSaved: () => void }) {
  const [selected, setSelected] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [version, setVersion] = useState(sceneVersion);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false; setLoading(true);
    api<{ asset_id: string | null; scene_version: number }>(`/scenes/${sceneId}/scene-master`).then(data => { if (!cancelled) { setSelected(data.asset_id || ""); setSaved(data.asset_id || ""); setVersion(data.scene_version); setLoading(false); } }).catch(error => { if (!cancelled) notify(errorMessage(error), "error"); });
    return () => { cancelled = true; };
  }, [sceneId, sceneVersion]);
  async function save() {
    if (!window.confirm("更改关联会使本场景各镜头旧首帧失效，历史素材保留。确认保存？")) return;
    setBusy(true);
    try { await api(`/scenes/${sceneId}/scene-master`, { method: "PUT", body: JSON.stringify({ expected_version: version, asset_id: selected || null }) }); setSaved(selected); notify("场景关联已保存，生成首帧时自动携带母版"); onSaved(); }
    catch (error) { notify(errorMessage(error), "error"); } finally { setBusy(false); }
  }
  const current = assets.find(a => a.id === saved);
  return <div className="scene-master-binding"><Field label="场景母版" hint="在左侧「场景资产」上传母版。同一地点的多个场景可选择同一份。"><select disabled={locked || busy || loading} value={selected} onChange={e => setSelected(e.target.value)}><option value="">不使用场景母版</option>{assets.map(asset => <option key={asset.id} value={asset.id}>{asset.name} · V{asset.version}</option>)}</select></Field><button className="soft-button" disabled={locked || busy || loading || selected === saved} onClick={() => void save()}>保存场景关联</button>{current && <img src={mediaUrl(current.master_reference_path) || ""} alt={`${current.name} 已关联母版`} />}</div>;
}
