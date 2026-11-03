import { Activity, AlertCircle, ArrowUpRight, CheckCircle2, Clock3, RefreshCw, Video } from "lucide-react";
import { useState } from "react";
import { api, errorMessage, mediaUrl } from "../api";
import { Callout, Empty, Modal } from "../components";
import type { Generation, Project } from "../types";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

const statusNames: Record<string, string> = { queued: "排队中", submitting: "提交中", submitted: "已提交", processing: "生成中", downloading: "下载中", ready: "已持久化", succeeded: "已完成", configuration_required: "需要配置", download_failed: "下载失败", unknown: "结果待核对", failed: "失败", canceled: "已取消", cancelled: "已取消" };

export function HistoryPage({ generations, projects, onChanged, notify }: { generations: Generation[]; projects: Project[]; onChanged: (generation: Generation) => void; notify: (message: string, kind?: "success" | "error") => void }) {
  const [filter, setFilter] = useState("all");
  const [refreshing, setRefreshing] = useState("");
  const [previewId, setPreviewId] = useState("");
  const preview = generations.find(item => item.id === previewId);
  const previewUrl = mediaUrl(preview?.output_path || null);
  const filtered = generations.filter((item) => filter === "all" || (filter === "succeeded" ? ["succeeded", "completed", "ready"].includes(item.status) : item.status === filter));

  async function refresh(generation: Generation) {
    setRefreshing(generation.id);
    try {
      const updated = await api<Generation>(`/generations/${generation.id}/refresh`, { method: "POST" });
      onChanged(updated); notify("任务状态已更新");
    } catch (error) { notify(errorMessage(error), "error"); }
    finally { setRefreshing(""); }
  }

  return (
    <div className="page-stack">
      {filtered.filter(item => item.review_warning).map(item => <Callout tone="warning" key={item.id}>{item.shot_id} · {item.review_warning}（任务 {item.id}）</Callout>)}
      <section className="page-intro compact"><div><span className="eyebrow">GENERATION LEDGER</span><h2>生成历史</h2><p>每次生成都是独立候选片段；先预览，再下载到剪辑软件决定采用哪些部分。</p></div><div className="filter-tabs"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部</button><button className={filter === "processing" ? "active" : ""} onClick={() => setFilter("processing")}>进行中</button><button className={filter === "succeeded" ? "active" : ""} onClick={() => setFilter("succeeded")}>已完成</button><button className={filter === "failed" ? "active" : ""} onClick={() => setFilter("failed")}>失败</button></div></section>
      {filtered.length ? <div className="history-table-wrap"><table className="history-table"><thead><tr><th>任务 / 镜头</th><th>项目</th><th>规格</th><th>状态</th><th>创建时间</th><th /></tr></thead><tbody>{filtered.map((generation) => {
        const canRefresh = (["queued", "submitted", "processing"].includes(generation.status) || generation.provider === "minimax" && generation.status === "succeeded") && Boolean(generation.external_id);
        const project = projects.find((item) => item.id === generation.project_id);
        const outputUrl = mediaUrl(generation.output_path);
        return <tr key={generation.id}><td><div className="task-cell"><span className={`task-icon ${generation.status}`}>{generation.status === "succeeded" || generation.status === "ready" ? <CheckCircle2 size={17} /> : generation.status === "failed" || generation.status === "download_failed" ? <AlertCircle size={17} /> : <Activity size={17} />}</span><div><strong>{generation.shot_id}</strong><small>{generation.external_id || generation.id}</small></div></div></td><td>{project?.name || generation.project_id}</td><td><strong>{generation.model || "默认模型"}</strong><small>{generation.resolution || "—"} · {generation.duration || "—"}s</small></td><td><span className={`status-pill ${generation.status}`}>{statusNames[generation.status] || generation.status}</span>{generation.error_message && <small className="error-copy">{generation.error_message}</small>}</td><td><span className="date-cell"><Clock3 size={13} />{formatDate(generation.created_at)}</span></td><td><div className="row-actions">{canRefresh && <button className="icon-button" onClick={() => void refresh(generation)} aria-label="刷新任务"><RefreshCw className={refreshing === generation.id ? "spinning" : ""} size={15} /></button>}{outputUrl && <><button className="icon-button" onClick={() => setPreviewId(generation.id)} aria-label="预览候选片段"><Video size={15} /></button><a className="icon-button" href={outputUrl} target="_blank" rel="noreferrer" aria-label="打开视频"><ArrowUpRight size={15} /></a></>}</div></td></tr>;
      })}</tbody></table></div> : <Empty icon={<Video size={32} />} title="没有匹配的任务" copy="提交生成后，状态与结果会出现在这里。" />}
      {preview && previewUrl && <Modal title="候选片段预览" eyebrow={preview.model} onClose={() => setPreviewId("")}><video className="candidate-video" src={previewUrl} controls playsInline preload="metadata" /><Callout>只保留满意的部分即可。例如 6 秒素材里选 2 秒用作转场。不采用不会自动退还生成额度；当前工作台未内置时间线裁剪，请下载后在剪映或其他剪辑软件中微调。</Callout><div className="modal-actions">{preview.provider === "minimax" && <button className="ghost-button" disabled={refreshing === preview.id} onClick={() => void refresh(preview)}>更新过期视频链接</button>}<a className="soft-button" href={previewUrl} target="_blank" rel="noreferrer">打开原视频 / 另存为</a></div></Modal>}
    </div>
  );
}
