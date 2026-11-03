import { useEffect, useState, type FormEvent } from "react";
import { Clapperboard, Plus } from "lucide-react";
import { api, errorMessage, postJson } from "../api";
import { Empty, Field, SubmitButton } from "../components";
import type { Episode, Project } from "../types";

export function EpisodesPage({ project, notify, onReview }: {
  project?: Project; notify: (message: string, kind?: "success" | "error") => void;
  onReview: (episodeId: string) => void;
}) {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const nextNumber = Math.max(0, ...episodes.map(item => item.episode_number)) + 1;
  useEffect(() => {
    let cancelled = false;
    if (project) api<Episode[]>(`/projects/${project.id}/episodes`)
      .then(items => { if (!cancelled) setEpisodes(items); })
      .catch(error => notify(errorMessage(error), "error"));
    return () => { cancelled = true; };
  }, [project?.id]);
  async function create(event: FormEvent) {
    event.preventDefault(); if (!project) return;
    setBusy(true);
    try {
      const episode = await postJson<Episode>(`/projects/${project.id}/episodes`, {
        episode_number: nextNumber, title, summary, duration_target: 180,
      });
      setEpisodes(items => [...items, episode]); setTitle(""); setSummary("");
      notify(`第 ${episode.episode_number} 集已建立，尚未生成剧本或视频`);
    } catch (error) { notify(errorMessage(error), "error"); }
    finally { setBusy(false); }
  }
  if (!project) return <Empty icon={<Clapperboard />} title="先选择剧集项目" copy="一个项目对应一部剧集，每个故事放在其中的一集。" />;
  return <div className="page-stack">
    <section className="page-intro compact"><div><span className="eyebrow">SERIES / {project.name}</span><h2>分集目录</h2><p>项目管理整部剧集；每一集独立保存故事、审阅稿和分镜。角色库在项目内共享。</p></div><div className="count-stamp"><strong>{episodes.length}</strong><span>EPISODES</span></div></section>
    <div className="split-layout project-layout">
      <div className="card-grid">{episodes.map(episode => <article className="panel" key={episode.id}>
        <span className="eyebrow">EP {String(episode.episode_number).padStart(2, "0")} · {episode.status === "draft" ? "待审阅" : episode.status === "revising" ? "修订中" : "制作中"}</span>
        <h3>{episode.title}</h3><p>{episode.summary}</p><p className="muted">目标时长 {episode.duration_target} 秒 · {episode.script_id ? "已关联审阅稿" : "尚无剧本"}</p>
        <button className="soft-button" onClick={() => onReview(episode.id)}>打开本集故事与审阅</button>
      </article>)}</div>
      <form className="panel sticky-form" onSubmit={event => void create(event)}>
        <h3>添加第 {nextNumber} 集</h3><Field label="本集标题"><input required maxLength={240} value={title} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label="本集简介"><textarea rows={5} value={summary} onChange={e => setSummary(e.target.value)} /></Field>
        <SubmitButton busy={busy}><Plus size={16} />建立分集</SubmitButton>
        <p className="muted">新建其他类型的剧集，请到“项目”页建立独立项目。</p>
      </form>
    </div>
  </div>;
}
