import { useEffect, useState } from "react";
import { api, errorMessage, mediaUrl, postJson, uploadAsset } from "./api";
import { activeElementStatuses, bindingLabel } from "./characterBindings";
import { Callout, Field, Modal } from "./components";
import type { Character, CharacterBindingState } from "./types";

export function CharacterReferenceModal({ character: initial, onClose, onSaved, notify }: {
  character: Character; onClose: () => void; onSaved: () => void;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [character, setCharacter] = useState(initial);
  const [master, setMaster] = useState(initial.master_reference_path || "");
  const [additional, setAdditional] = useState(initial.reference_assets?.additional_reference_paths || []);
  const [masterFile, setMasterFile] = useState<File | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState(initial.name.slice(0, 20));
  const [description, setDescription] = useState(initial.face_description.slice(0, 100));
  const [state, setState] = useState<CharacterBindingState | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const binding = state?.binding;
  const active = Boolean(binding && activeElementStatuses.includes(binding.status));
  const dirty = Boolean(masterFile || files.length || master !== (character.master_reference_path || "")
    || additional.join("\n") !== (character.reference_assets?.additional_reference_paths || []).join("\n"));

  useEffect(() => {
    let cancelled = false;
    api<CharacterBindingState>(`/characters/${character.id}/kling-element`)
      .then(data => { if (!cancelled) setState(data); })
      .catch(err => { if (!cancelled) setError(errorMessage(err)); });
    return () => { cancelled = true; };
  }, [character.id]);

  // Poll only while this dialog is visible, serially, for at most two minutes.
  useEffect(() => {
    if (!binding || !["submitted", "processing"].includes(binding.status)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let count = 0;
    async function poll() {
      try {
        const next = await postJson<CharacterBindingState>(`/characters/${character.id}/kling-element/refresh`, {});
        if (cancelled) return;
        setState(next);
        if (next.binding && ["submitted", "processing"].includes(next.binding.status) && ++count < 12) {
          timer = setTimeout(() => void poll(), 10000);
        } else onSaved();
      } catch (err) { if (!cancelled) setError(errorMessage(err)); }
    }
    timer = setTimeout(() => void poll(), 10000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [binding?.external_task_id]);

  async function save() {
    setBusy(true); setError(""); setConfirmed(false);
    try {
      const main = masterFile ? (await uploadAsset(character.project_id, "character_reference", masterFile)).path : master;
      const extra = [...additional];
      for (const file of files) extra.push((await uploadAsset(character.project_id, "character_reference", file)).path);
      const saved = await api<Character>(`/characters/${character.id}/references`, {
        method: "PUT", body: JSON.stringify({ expected_version: character.version,
          master_reference_path: main, additional_reference_paths: extra }),
      });
      setCharacter(saved); setMaster(main); setAdditional(extra); setMasterFile(null); setFiles([]);
      setState(await api<CharacterBindingState>(`/characters/${character.id}/kling-element`));
      onSaved(); notify("参考图已保存在本地，尚未上传可灵");
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function submit() {
    if (!confirmed || dirty) return;
    setBusy(true); setError("");
    try {
      const next = await postJson<CharacterBindingState>(`/characters/${character.id}/kling-element`, {
        expected_version: character.version, element_name: name, element_description: description,
        confirmed: true, replace_existing: Boolean(binding?.element_id),
      });
      setState(next); setConfirmed(false); onSaved();
      notify(next.binding?.error_message || "主体创建任务已提交；不会生成视频", next.binding?.error_message ? "error" : "success");
    } catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  async function refresh() {
    setBusy(true); setError("");
    try { setState(await postJson<CharacterBindingState>(`/characters/${character.id}/kling-element/refresh`, {})); onSaved(); }
    catch (err) { setError(errorMessage(err)); }
    finally { setBusy(false); }
  }

  return <Modal title={`${character.name} · 参考图与可灵主体`} eyebrow="LOCAL ASSETS → KLING ELEMENT" onClose={() => !busy && onClose()}>
    <div className="modal-form character-reference-form">
      <Callout>先保存到本地并审阅，再明确确认上传可灵。需要 1 张正面母版 + 1–3 张不同角度参考图；每张 JPG/PNG ≤10 MB，两边 ≥300px，宽高比 2:5–5:2。母版不会自动替代镜头首帧。</Callout>
      <Field label="正面母版图"><ReferencePreview path={master} file={masterFile} label="正面母版" /><input key={character.version} disabled={busy || active} type="file" accept="image/png,image/jpeg" onChange={e => { setMasterFile(e.target.files?.[0] || null); setConfirmed(false); }} /></Field>
      <Field label="不同角度参考图（1–3 张）">
        <div className="reference-thumbnails">{additional.map((path, index) => <div key={path}><ReferencePreview path={path} label={`参考图 ${index + 1}`} /><button type="button" className="text-button" disabled={busy || active} onClick={() => { setAdditional(additional.filter(p => p !== path)); setConfirmed(false); }}>移出本次参考</button></div>)}{files.map((file, index) => <ReferencePreview key={`${file.name}-${index}`} file={file} label={file.name} />)}</div>
        <input key={character.version} disabled={busy || active} type="file" multiple accept="image/png,image/jpeg" onChange={e => { setFiles(Array.from(e.target.files || [])); setConfirmed(false); }} />
        <small>补充图总计 {additional.length + files.length}/3 张；请确认是同一个角色。</small>
      </Field>
      <button className="soft-button" type="button" disabled={busy || active || !dirty || !(master || masterFile) || additional.length + files.length > 3} onClick={() => void save()}>{busy ? "处理中…" : "保存参考图到本地"}</button>
      <div className="section-rule"><span>可灵主体绑定</span></div>
      <strong>{bindingLabel(character, binding || undefined)}</strong>
      {binding && <div className="binding-details"><span>主体 ID：{binding.element_id || "等待返回"}</span><span>任务：{binding.task_id || binding.external_task_id}</span></div>}
      {state?.issue && <p className="muted">{state.issue}</p>}
      {binding?.error_message && <Callout tone="warning">{binding.error_message}</Callout>}
      {state && !state.configured && <Callout tone="warning">{state.configuration_issues.join("；")}</Callout>}
      <Field label="上传到可灵的主体名（最多 20 字）"><input maxLength={20} value={name} disabled={busy || active} onChange={e => { setName(e.target.value); setConfirmed(false); }} /></Field>
      <Field label="主体视觉描述（最多 100 字）" hint="只上传这段描述和参考图片，不会上传整个剧本。"><textarea maxLength={100} rows={3} value={description} disabled={busy || active} onChange={e => { setDescription(e.target.value); setConfirmed(false); }} /></Field>
      {!active && <label className="quota-confirm"><input type="checkbox" checked={confirmed} disabled={busy || dirty} onChange={e => setConfirmed(e.target.checked)} />我已审阅已保存的图片，确认上传到当前可灵 API 账户；费用以可灵账单为准。{binding?.element_id ? "重新创建会替换本地绑定，不会删除原远端主体。" : ""}</label>}
      {dirty && <small>有未保存的参考图，请先保存再确认上传。</small>}
      {error && <Callout tone="warning">{error}</Callout>}
      <div className="modal-actions">
        {binding && <button className="soft-button" disabled={busy} onClick={() => void refresh()}>刷新主体状态</button>}
        <button className="primary-button" disabled={busy || active || dirty || !confirmed || !state?.configured || !master || additional.length < 1 || !name.trim() || !description.trim()} onClick={() => void submit()}>{binding?.element_id ? "确认重新创建可灵主体" : "确认上传并创建可灵主体"}</button>
      </div>
      <small>未确认创建前不请求可灵；关闭窗口不会取消已提交的任务。结果不确定时请查询原任务，不能直接重试创建。</small>
    </div>
  </Modal>;
}

function ReferencePreview({ path, file, label }: { path?: string; file?: File | null; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const objectUrl = file ? URL.createObjectURL(file) : null;
    setUrl(objectUrl || mediaUrl(path || null));
    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [file, path]);
  return url ? <figure className="reference-preview"><img src={url} alt={label} /><figcaption>{label}</figcaption></figure> : <small>尚未上传母版图</small>;
}
