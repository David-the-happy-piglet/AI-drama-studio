import { useRef, useState } from "react";
import { errorMessage, postJson } from "./api";
import { Field } from "./components";

export function ExternalStoryboardImport({ projectId, episodeId, disabled, onImported, notify }: {
  projectId: string; episodeId: string; disabled: boolean;
  onImported: (id: string) => Promise<void>;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const attempt = useRef<{ input: string; token: string } | null>(null);
  async function importDraft() {
    if (busy || disabled) return;
    setBusy(true);
    try {
      const payload = JSON.parse(text.replace(/^\s*```(?:json)?\s*\n/i, "").replace(/\n```\s*$/, ""));
      if (!payload || typeof payload !== "object" || !payload.title || !payload.source_idea || !payload.draft) {
        throw new Error("请提供包含 title、source_idea 和 draft（screenplay、scenes）的 JSON。");
      }
      const input = JSON.stringify([projectId, episodeId, payload]);
      if (attempt.current?.input !== input) attempt.current = { input, token: crypto.randomUUID() };
      const result = await postJson<{ id: string }>(
        `/projects/${projectId}/external-storyboards${episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ""}`,
        { ...payload, request_token: attempt.current.token },
      );
      await onImported(result.id);
      notify("已导入外部分镜，保留剧本和台词原文，可直接审阅。");
      setText("");
    } catch (error) { notify(errorMessage(error), "error"); }
    finally { setBusy(false); }
  }
  return <section className="panel">
    <h3>导入 Claude / 外部分镜</h3>
    <p className="muted">粘贴分镜 JSON，直接建立新审阅稿。不会调用文本模型重新拆分，也不会启动图片或视频任务。角色和造型须使用当前项目已有 ID。</p>
    <Field label="分镜 JSON" hint="结构：title、source_idea、draft.screenplay、draft.scenes。每场包含 location、shots；镜头需有 order_index、duration、shot_type、visual_description。">
      <textarea rows={7} value={text} disabled={disabled || busy} onChange={e => setText(e.target.value)} placeholder={'{"title":"故事名","source_idea":"故事简介","draft":{"screenplay":"完整剧本","scenes":[]}}'} />
    </Field>
    <button className="primary-button" disabled={disabled || busy || !text.trim()} onClick={() => void importDraft()}>{busy ? "正在导入…" : "导入为新审阅稿"}</button>
    {disabled && <p className="muted">请先保存当前编辑，再导入新稿。</p>}
  </section>;
}
