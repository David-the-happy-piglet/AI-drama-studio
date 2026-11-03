import { Boxes, Clapperboard, Command, Film, Sparkles } from "lucide-react";
import { ProviderStatusList } from "../ProviderStatusList";
import type { PlatformStatus } from "../providerStatus";
import type { Generation, Project } from "../types";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function DashboardPage({
  projects,
  generations,
  platforms,
  loading,
  navigate,
}: {
  projects: Project[];
  generations: Generation[];
  platforms: PlatformStatus[];
  loading: boolean;
  navigate: (page: string) => void;
}) {
  const successful = generations.filter((item) => ["succeeded", "completed", "ready"].includes(item.status)).length;
  return (
    <>
      <section className="hero-panel">
        <div className="hero-copy">
          <span className="hero-kicker"><Film size={14} /> SHOT-FIRST WORKFLOW</span>
          <h2>把每一镜，<br />做成可控资产。</h2>
          <p>从故事简介开始，审阅剧本与分镜，再选择平台制作。角色、造型和生成记录独立保存，失败只重做当前镜头。</p>
          <div className="hero-actions">
            <button className="light-button" onClick={() => navigate("story")}><Sparkles size={17} /> 从故事开始</button>
            <button className="text-button inverted" onClick={() => navigate("storyboard")}>查看工作流 <span>↗</span></button>
          </div>
        </div>
        <div className="hero-visual" aria-hidden="true">
          <div className="frame frame-back"><span>SC / 014</span></div>
          <div className="frame frame-mid"><span>SC / 015</span></div>
          <div className="frame frame-front">
            <div className="focus-reticle" />
            <span>EP01 · SH016</span><strong>READY</strong>
          </div>
        </div>
      </section>

      <section className="metrics-grid">
        <article className="metric-card"><span>项目</span><strong>{projects.length}</strong><small>ACTIVE PROJECTS</small></article>
        <article className="metric-card"><span>生成记录</span><strong>{generations.length}</strong><small>RECENT ATTEMPTS</small></article>
        <article className="metric-card"><span>已完成</span><strong>{successful}</strong><small>USABLE CLIPS</small></article>
        <article className="metric-card accent-card">
          <span>平台后端配置</span>
          <strong className="status-strong">{loading ? "读取中" : platforms.every(item => item.status === "状态未知") ? "状态未知" : `${platforms.filter(item => item.configured).length} / ${platforms.length} 已配置`}</strong>
          <small>配置状态不等于生成验证</small>
        </article>
      </section>

      <section className="content-grid">
        <article className="panel recent-panel">
          <div className="panel-heading"><div><span className="eyebrow">LATEST RUNS</span><h3>最近生成</h3></div><button className="text-button" onClick={() => navigate("history")}>查看全部</button></div>
          {generations.length ? (
            <div className="run-list">
              {generations.slice(0, 5).map((run) => (
                <div className="run-row" key={run.id}>
                  <div className="run-thumb"><Clapperboard size={19} /></div>
                  <div className="run-main"><strong>{run.shot_id}</strong><span>{run.provider} · {run.model || "默认模型"}</span></div>
                  <span className={`status-pill ${run.status}`}>{run.status}</span>
                  <time>{formatDate(run.created_at)}</time>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state"><Boxes size={27} /><strong>还没有生成记录</strong><span>创建项目和镜头后，从关键帧开始第一条视频。</span></div>
          )}
        </article>

        <article className="panel setup-panel">
          <div className="panel-heading"><div><span className="eyebrow">PROVIDER CHECK</span><h3>平台接入状态</h3></div><Command size={20} /></div>
          <ProviderStatusList platforms={platforms} />
          <button className="secondary-button" onClick={() => navigate("cli")}>打开接入指南</button>
        </article>
      </section>
    </>
  );
}
