import { ImagePlus, Plus, Shirt, UserRound, Users } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { api, errorMessage, mediaUrl, postJson, uploadAsset } from "../api";
import { Empty, Field, Modal, SubmitButton } from "../components";
import type { Character, Outfit, Project } from "../types";
import { CharacterReferenceModal } from "../CharacterReferenceModal";
import { bindingLabel } from "../characterBindings";
import { CharacterPortraitModal } from "../CharacterPortraitModal";

export function CharactersPage({
  project,
  notify,
}: {
  project?: Project;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [gender, setGender] = useState("");
  const [face, setFace] = useState("");
  const [personality, setPersonality] = useState("");
  const [visualStyle, setVisualStyle] = useState("电影写实");
  const [reference, setReference] = useState<File | null>(null);
  const [outfitCharacter, setOutfitCharacter] = useState<Character | null>(null);
  const [referenceCharacter, setReferenceCharacter] = useState<Character | null>(null);
  const [portraitCharacter, setPortraitCharacter] = useState<Character | null>(null);

  async function loadCharacters() {
    if (!project) return setCharacters([]);
    setLoading(true);
    try {
      setCharacters(await api<Character[]>(`/projects/${project.id}/characters`));
    } catch (error) {
      notify(errorMessage(error), "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadCharacters(); }, [project?.id]);

  async function createCharacter(event: FormEvent) {
    event.preventDefault();
    if (!project) return;
    setBusy(true);
    try {
      const uploaded = reference
        ? await uploadAsset(project.id, "character_reference", reference)
        : null;
      await postJson<Character>(`/projects/${project.id}/characters`, {
        name,
        apparent_age: age,
        gender,
        face_description: face,
        personality,
        visual_style: visualStyle,
        master_reference_path: uploaded?.path ?? null,
      });
      setName(""); setAge(""); setGender(""); setFace(""); setPersonality(""); setReference(null);
      await loadCharacters();
      notify("角色资产已保存");
    } catch (error) {
      notify(errorMessage(error), "error");
    } finally {
      setBusy(false);
    }
  }

  if (!project) {
    return <Empty icon={<Users size={32} />} title="请先选择项目" copy="在“项目”页创建或选择一部短剧。" />;
  }

  return (
    <div className="page-stack">
      <section className="page-intro compact">
        <div><span className="eyebrow">IDENTITY BIBLE / {project.name}</span><h2>角色资产</h2><p>固定脸型、体态、性格与母版参考图，减少跨镜头漂移。</p></div>
      </section>
      <section className="split-layout asset-layout">
        <div className="character-grid">
          {characters.length ? characters.map((character, index) => {
            const image = mediaUrl(character.master_reference_path);
            return (
              <article className="character-card" key={character.id}>
                <div className={`character-portrait portrait-${index % 4}`}>
                  {image ? <img src={image} alt={`${character.name} 角色参考`} /> : <UserRound size={45} />}
                  <span>V{character.version}</span>
                </div>
                <div className="character-copy">
                  <div><span className="eyebrow">{character.apparent_age || "AGE UNSET"}</span><h3>{character.name}</h3></div>
                  <p>{character.face_description || character.personality || "等待补充角色视觉锚点。"}</p>
                  <div className="chip-row"><span>{character.visual_style || "未定义风格"}</span>{character.gender && <span>{character.gender}</span>}</div>
                  <button className="soft-button" onClick={() => setOutfitCharacter(character)}><Shirt size={15} /> 添加造型</button>
                  <button className="soft-button" onClick={() => setReferenceCharacter(character)}><ImagePlus size={15} /> 参考图与可灵主体</button>
                  <button className="soft-button" onClick={() => setPortraitCharacter(character)}><ImagePlus size={15} /> {character.master_reference_path ? "基于母版参考图生成" : "母版 prompt · 生成候选图"}</button>
                  <small>{bindingLabel(character, character.provider_bindings?.find(item => item.provider === "kling"))}</small>
                </div>
              </article>
            );
          }) : <Empty icon={<Users size={30} />} title={loading ? "读取中…" : "还没有角色"} copy="在右侧创建第一位核心角色。" />}
        </div>

        <form className="panel sticky-form" onSubmit={(event) => void createCharacter(event)}>
          <div className="panel-heading"><div><span className="eyebrow">NEW IDENTITY</span><h3>创建角色</h3></div><Plus size={19} /></div>
          <div className="field-grid two">
            <Field label="姓名"><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="林夏" /></Field>
            <Field label="外观年龄"><input value={age} onChange={(event) => setAge(event.target.value)} placeholder="25 岁左右" /></Field>
            <Field label="性别表达"><input value={gender} onChange={(event) => setGender(event.target.value)} placeholder="女性" /></Field>
            <Field label="视觉风格"><input value={visualStyle} onChange={(event) => setVisualStyle(event.target.value)} /></Field>
          </div>
          <Field label="面部与识别特征"><textarea required value={face} onChange={(event) => setFace(event.target.value)} placeholder="脸型、五官、发型、独特标记…" rows={4} /></Field>
          <Field label="性格与表演基调"><textarea value={personality} onChange={(event) => setPersonality(event.target.value)} placeholder="克制、警觉，情绪不外露…" rows={3} /></Field>
          <Field label="母版参考图" hint="JPG / PNG / WebP，文件保存在本机项目目录。">
            <label className="file-drop"><ImagePlus size={19} /><span>{reference?.name || "选择一张清晰正面参考图"}</span><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => setReference(event.target.files?.[0] ?? null)} /></label>
          </Field>
          <SubmitButton busy={busy}><Plus size={16} /> 保存角色</SubmitButton>
        </form>
      </section>
      {outfitCharacter && <OutfitModal character={outfitCharacter} onClose={() => setOutfitCharacter(null)} notify={notify} />}
      {referenceCharacter && <CharacterReferenceModal key={referenceCharacter.id} character={referenceCharacter} onClose={() => setReferenceCharacter(null)} onSaved={() => void loadCharacters()} notify={notify} />}
      {portraitCharacter && <CharacterPortraitModal key={portraitCharacter.id} character={portraitCharacter} onClose={() => setPortraitCharacter(null)} onSaved={() => void loadCharacters()} onReferences={character => { setPortraitCharacter(null); setReferenceCharacter(character); }} notify={notify} />}
    </div>
  );
}

function OutfitModal({ character, onClose, notify }: { character: Character; onClose: () => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [components, setComponents] = useState("");
  const [colors, setColors] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true);
    try {
      await postJson<Outfit>(`/characters/${character.id}/outfits`, {
        name,
        description,
        components: components.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
        color_palette: colors.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
      });
      notify(`已为 ${character.name} 保存造型`); onClose();
    } catch (error) { notify(errorMessage(error), "error"); }
    finally { setBusy(false); }
  }
  return (
    <Modal title={`${character.name} · 新造型`} eyebrow="WARDROBE" onClose={onClose}>
      <form className="modal-form" onSubmit={(event) => void submit(event)}>
        <Field label="造型名"><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="雨夜通勤装" /></Field>
        <Field label="完整描述"><textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} placeholder="剪裁、材质、新旧程度、穿着方式…" /></Field>
        <Field label="服装组件"><input value={components} onChange={(event) => setComponents(event.target.value)} placeholder="黑色风衣，白衬衫，银色腕表" /></Field>
        <Field label="色板"><input value={colors} onChange={(event) => setColors(event.target.value)} placeholder="#171717，冷灰，暗红" /></Field>
        <div className="modal-actions"><button className="ghost-button" type="button" onClick={onClose}>取消</button><SubmitButton busy={busy}>保存造型</SubmitButton></div>
      </form>
    </Modal>
  );
}
