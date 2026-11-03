import { ArrowUpRight, Film, FolderKanban, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { errorMessage, postJson } from "../api";
import { Empty, Field, SubmitButton } from "../components";
import type { Project } from "../types";

export function ProjectsPage({
  projects,
  selectedProjectId,
  onSelect,
  onCreated,
  notify,
}: {
  projects: Project[];
  selectedProjectId: string;
  onSelect: (id: string) => void;
  onCreated: (project: Project) => void;
  notify: (message: string, kind?: "success" | "error") => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [genre, setGenre] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const project = await postJson<Project>("/projects", { name, description,
        settings_json: { project_type: "series", genre } });
      setName("");
      setDescription("");
      onCreated(project);
      notify("项目已创建");
    } catch (error) {
      notify(errorMessage(error), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-stack">
      <section className="page-intro">
        <div><span className="eyebrow">PRODUCTION SLATES</span><h2>剧集项目库</h2><p>一个项目是一部剧集，项目下建立多集故事；不同类型的剧集拥有各自的角色和制作资料。</p></div>
        <div className="count-stamp"><strong>{String(projects.length).padStart(2, "0")}</strong><span>PROJECTS</span></div>
      </section>

      <section className="split-layout project-layout">
        <div className="card-grid">
          {projects.length ? projects.map((project, index) => (
            <article className={project.id === selectedProjectId ? "project-card selected" : "project-card"} key={project.id}>
              <div className={`project-poster poster-${index % 4}`}>
                <span>PRJ / {String(index + 1).padStart(2, "0")}</span>
                <Film size={32} />
              </div>
              <div className="project-card-copy">
                <span className="eyebrow">{String(project.settings_json.genre || project.status)}</span>
                <h3>{project.name}</h3>
                <p>{project.description || "尚未填写项目简介。"}</p>
                <button className="text-button" onClick={() => onSelect(project.id)}>
                  {project.id === selectedProjectId ? "当前项目" : "设为当前"} <ArrowUpRight size={13} />
                </button>
              </div>
            </article>
          )) : <Empty icon={<FolderKanban size={30} />} title="建立第一部短剧" copy="从右侧填写一个项目名称开始。" />}
        </div>

        <form className="panel sticky-form" onSubmit={(event) => void submit(event)}>
          <div className="panel-heading"><div><span className="eyebrow">NEW PROJECT</span><h3>新建项目</h3></div><Plus size={19} /></div>
          <Field label="项目名称">
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：雨夜来信" required maxLength={200} />
          </Field>
          <Field label="故事简介" hint="后续可继续扩充剧本结构。">
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="一句话写清人物、冲突和钩子。" rows={5} />
          </Field>
          <Field label="剧集类型"><input value={genre} onChange={e => setGenre(e.target.value)} placeholder="例如：科幻寓言、悬疑、都市喜剧" /></Field>
          <SubmitButton busy={busy}><Plus size={16} /> 创建项目</SubmitButton>
        </form>
      </section>
    </div>
  );
}
