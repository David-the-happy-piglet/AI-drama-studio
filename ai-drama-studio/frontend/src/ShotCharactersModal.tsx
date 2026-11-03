import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "./api";
import { bindingLabel } from "./characterBindings";
import { Callout, Modal, SubmitButton } from "./components";
import { characterSelectionPayload } from "./shotCharacters";
import type { Character, Shot } from "./types";

export function ShotCharactersModal({ projectId, shot, onClose, onSaved }: {
  projectId: string;
  shot: Shot;
  onClose: () => void;
  onSaved: (shot: Shot) => void;
}) {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [ids, setIds] = useState(() => shot.characters.map(item => item.character_id));
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setLoadError("");
    api<Character[]>(`/projects/${projectId}/characters`)
      .then(data => { if (!cancelled) setCharacters(data); })
      .catch(reason => { if (!cancelled) setLoadError(errorMessage(reason)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, attempt]);

  const missingIds = ids.filter(id => !characters.some(character => character.id === id));
  const changed = ids.length !== shot.characters.length || ids.some(id => !shot.characters.some(item => item.character_id === id));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || loading || loadError || conflict || !changed || missingIds.length) return;
    setBusy(true); setError("");
    try {
      const updated = await api<Shot>(`/shots/${shot.id}`, {
        method: "PATCH",
        body: JSON.stringify(characterSelectionPayload(shot, ids)),
      });
      onSaved(updated);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setConflict(true);
        setError("镜头已被其他操作更新，本次没有覆盖修改。请关闭窗口并刷新页面，再重新选择角色。");
      } else setError(errorMessage(reason));
    } finally { setBusy(false); }
  }

  return <Modal title="关联镜内角色" eyebrow={`SHOT / ${shot.id}`} onClose={() => { if (!busy) onClose(); }}>
    <form className="modal-form" onSubmit={event => void save(event)}>
      <Callout>只选择画面中出现的角色。电话声、旁白不用关联视觉主体；空镜可以全部取消。保存仅更新本镜头，不会创建可灵主体或生成视频。</Callout>
      <p>{shot.visual_description}</p>
      {loading ? <p role="status">正在读取本项目角色…</p> : loadError ? <div role="alert"><Callout tone="warning">{loadError}</Callout><button type="button" className="soft-button" onClick={() => setAttempt(value => value + 1)}>重新加载角色</button></div> : <>
        {characters.length ? <fieldset disabled={busy || conflict}><legend>镜内角色（可多选）</legend><div className="choice-grid">
          {characters.map(character => <label key={character.id} className={ids.includes(character.id) ? "choice active" : "choice"}>
            <input type="checkbox" checked={ids.includes(character.id)} onChange={event => {
              setError("");
              setIds(current => event.target.checked ? [...current, character.id] : current.filter(id => id !== character.id));
            }} />
            <span>{character.name}<small> · {bindingLabel(character, character.provider_bindings.find(binding => binding.provider === "kling"))}</small></span>
          </label>)}
        </div></fieldset> : <p>本项目还没有角色，请先到「角色资产」创建。</p>}
        {!!missingIds.length && <Callout tone="warning">已有镜头角色不在本项目列表中：{missingIds.join("、")}。为避免丢失关联，暂不能保存，请先核查角色数据。</Callout>}
        <small>已选 {ids.length} 个角色。保留角色原有造型；新增角色不自动指定造型。可灵主体是否可用，以保存后生成页的核对结果为准。</small>
      </>}
      {error && <div role="alert"><Callout tone="warning">{error}</Callout></div>}
      <div className="modal-actions">
        <button type="button" className="ghost-button" disabled={busy} onClick={onClose}>取消</button>
        <SubmitButton busy={busy} disabled={loading || !!loadError || conflict || !changed || !!missingIds.length}>保存角色关联</SubmitButton>
      </div>
    </form>
  </Modal>;
}
