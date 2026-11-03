import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Aperture,
  Clapperboard,
  Command,
  FolderKanban,
  LayoutDashboard,
  Plus,
  RefreshCw,
  Sparkles,
  Users,
} from "lucide-react";
import { api, errorMessage } from "./api";
import { getProviderStatuses } from "./providerStatus";
import { CharactersPage } from "./pages/CharactersPage";
import { CliPage } from "./pages/CliPage";
import { DashboardPage } from "./pages/DashboardPage";
import { GeneratePage } from "./pages/GeneratePage";
import { HistoryPage } from "./pages/HistoryPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { EpisodesPage } from "./pages/EpisodesPage";
import { StoryboardPage } from "./pages/StoryboardPage";
import { StoryPage } from "./pages/StoryPage";
import { SceneAssetsPage } from "./pages/SceneAssetsPage";
import type { Generation, Project, SeedanceAccount, VideoProvider } from "./types";

const navItems = [
  { id: "dashboard", label: "总览", icon: LayoutDashboard },
  { id: "projects", label: "项目", icon: FolderKanban },
  { id: "episodes", label: "分集目录", icon: Clapperboard },
  { id: "story", label: "故事与审阅", icon: Clapperboard },
  { id: "characters", label: "角色资产", icon: Users },
  { id: "scene-assets", label: "场景资产", icon: Clapperboard },
  { id: "storyboard", label: "分镜", icon: Clapperboard },
  { id: "generate", label: "镜头生成", icon: Sparkles },
  { id: "history", label: "生成历史", icon: Activity },
  { id: "cli", label: "平台接入", icon: Command },
] as const;

type PageId = typeof navItems[number]["id"];
const pageTitles: Record<PageId, string> = {
  dashboard: "制作总览",
  projects: "项目库",
  episodes: "剧集 → 分集目录",
  story: "故事 → 剧本 → 分镜",
  characters: "角色资产",
  "scene-assets": "场景母版资产",
  storyboard: "分镜台",
  generate: "镜头生成",
  history: "生成历史",
  cli: "可灵 / Seedance / MiniMax 接入",
};

function App() {
  const [activeNav, setActiveNav] = useState<PageId>("dashboard");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState(() => localStorage.getItem("ai-drama-project") || "");
  const [selectedShotId, setSelectedShotId] = useState("");
  const [selectedEpisodeId, setSelectedEpisodeId] = useState("");
  const [generations, setGenerations] = useState<Generation[]>([]);
  const [providers, setProviders] = useState<VideoProvider[]>([]);
  const [account, setAccount] = useState<SeedanceAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ message: string; kind: "success" | "error" } | null>(null);

  function notify(message: string, kind: "success" | "error" = "success") {
    setToast({ message, kind });
    window.setTimeout(() => setToast((current) => current?.message === message ? null : current), 3800);
  }

  async function loadWorkspace() {
    setLoading(true);
    try {
      const [projectData, generationData, providerData, accountData] = await Promise.all([
        api<Project[]>("/projects"),
        api<Generation[]>("/generations?limit=100"),
        api<VideoProvider[]>("/providers/video"),
        api<SeedanceAccount>("/providers/video/seedance/account").catch(error => ({ authenticated: false, error: `Seedance API 鉴权查询失败：${errorMessage(error)}` })),
      ]);
      setProjects(projectData);
      setGenerations(generationData);
      setProviders(providerData);
      setAccount(accountData);
      setSelectedProjectId((current) => projectData.some((item) => item.id === current) ? current : projectData[0]?.id || "");
    } catch (error) {
      setProviders([]);
      setAccount(null);
      notify(`后端连接失败：${errorMessage(error)}`, "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadWorkspace(); }, []);
  useEffect(() => {
    if (selectedProjectId) localStorage.setItem("ai-drama-project", selectedProjectId);
    else localStorage.removeItem("ai-drama-project");
  }, [selectedProjectId]);

  const seedance = useMemo(() => providers.find((provider) => provider.provider === "seedance"), [providers]);
  const platforms = getProviderStatuses(providers, account, generations, loading);
  const configuredPlatforms = platforms.filter(platform => platform.configured);
  const selectedProject = projects.find((project) => project.id === selectedProjectId);

  function navigate(page: string) { setActiveNav(page as PageId); }
  function openGenerate(shotId: string, episodeId: string) { setSelectedShotId(shotId); setSelectedEpisodeId(episodeId); setActiveNav("generate"); }
  function handleProjectCreated(project: Project) { setProjects((items) => [project, ...items]); setSelectedProjectId(project.id); }
  function handleGenerationChanged(generation: Generation) { setGenerations((items) => [generation, ...items.filter((item) => item.id !== generation.id)]); }

  const page = (() => {
    switch (activeNav) {
      case "projects": return <ProjectsPage projects={projects} selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} onCreated={handleProjectCreated} notify={notify} />;
      case "episodes": return <EpisodesPage key={selectedProjectId} project={selectedProject} notify={notify} onReview={id => { setSelectedEpisodeId(id); setActiveNav("story"); }} />;
      case "story": return <StoryPage key={selectedProjectId} project={selectedProject} preselectedEpisodeId={selectedEpisodeId} notify={notify} onPublished={id => { setSelectedEpisodeId(id); setActiveNav("storyboard"); }} />;
      case "characters": return <CharactersPage key={selectedProjectId} project={selectedProject} notify={notify} />;
      case "scene-assets": return <SceneAssetsPage key={selectedProjectId} project={selectedProject} notify={notify} />;
      case "storyboard": return <StoryboardPage key={selectedProjectId} project={selectedProject} preselectedEpisodeId={selectedEpisodeId} notify={notify} onGenerate={openGenerate} onReview={id => { setSelectedEpisodeId(id); setActiveNav("story"); }} />;
      case "generate": return <GeneratePage key={selectedProjectId} project={selectedProject} account={account} preselectedShotId={selectedShotId} preselectedEpisodeId={selectedEpisodeId} notify={notify} onSubmitted={(generation) => { handleGenerationChanged(generation); setActiveNav("history"); }} />;
      case "history": return <HistoryPage generations={generations} projects={projects} onChanged={handleGenerationChanged} notify={notify} />;
      case "cli": return <CliPage platforms={platforms} seedance={seedance} account={account} loading={loading} onRefresh={() => void loadWorkspace()} notify={notify} />;
      default: return <DashboardPage projects={projects} generations={generations} platforms={platforms} loading={loading} navigate={navigate} />;
    }
  })();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Aperture size={21} /></div><div><strong>AI DRAMA</strong><span>STUDIO / 01</span></div></div>
        <nav className="nav-list" aria-label="主要导航">
          {navItems.map(({ id, label, icon: Icon }) => <button className={activeNav === id ? "nav-item active" : "nav-item"} key={id} onClick={() => setActiveNav(id)}><Icon size={18} /><span>{label}</span></button>)}
        </nav>
        <div className="sidebar-footer"><div className={!loading && configuredPlatforms.length ? "system-dot" : "system-dot waiting"} /><div><strong>{loading ? "平台状态读取中" : providers.length ? `${configuredPlatforms.length} 个平台已配置` : "平台状态未知"}</strong><span>Kling · Seedance · MiniMax</span></div></div>
      </aside>

      <main className="main-canvas">
        <header className="topbar">
          <div><span className="eyebrow">PRODUCTION CONTROL</span><h1>{pageTitles[activeNav]}</h1></div>
          <div className="topbar-actions">
            {projects.length > 0 && activeNav !== "projects" && <label className="project-switcher"><span>当前项目</span><select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>{projects.map((project) => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label>}
            <button className="icon-button" onClick={() => void loadWorkspace()} aria-label="刷新"><RefreshCw size={17} className={loading ? "spinning" : ""} /></button>
            <button className="primary-button" onClick={() => setActiveNav("projects")}><Plus size={17} /> 新建项目</button>
          </div>
        </header>
        {page}
      </main>
      {toast && <div className={`toast ${toast.kind}`} role="status"><span />{toast.message}</div>}
    </div>
  );
}

export default App;
