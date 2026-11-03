import { ExternalLink, RefreshCw } from "lucide-react";
import { Callout, CodeBlock } from "../components";
import { ProviderStatusList } from "../ProviderStatusList";
import type { PlatformStatus } from "../providerStatus";
import type { SeedanceAccount, VideoProvider } from "../types";

export function CliPage({ platforms, seedance, account, loading, onRefresh, notify }: { platforms: PlatformStatus[]; seedance?: VideoProvider; account: SeedanceAccount | null; loading: boolean; onRefresh: () => void; notify: (message: string) => void }) {
  return (
    <div className="page-stack">
      <section className="panel setup-panel"><div className="panel-heading"><h3>平台接入状态</h3><button className="icon-button" disabled={loading} onClick={onRefresh} aria-label="刷新平台状态"><RefreshCw className={loading ? "spinning" : ""} size={16} /></button></div><ProviderStatusList platforms={platforms} /></section>
      <section className="panel"><div className="panel-heading"><h3>MiniMax · Token Plan 素材</h3></div><p>在“镜头生成”选择 MiniMax，可制作空镜、转场、补充素材和试拍。订阅密钥仅由后端读取；每日赠送额度与旧工作台共用，每次生成前需自行核对剩余额度。</p><p>配置方式：后端设置 <code>MINIMAX_TOKEN_PLAN_KEY</code>，或通过 <code>MINIMAX_TOKEN_PLAN_ENV_FILE</code> 引用旧项目的私有配置文件，然后重启后端。不要把密钥填进前端。</p></section>
      <section className="panel"><div className="panel-heading"><h3>可灵 · 官方 API 接入</h3></div>
        <p>新版接口使用 API Key。到可灵开放平台创建密钥，配置后端环境并重启；在“镜头生成”选择可灵即可。当前适配 Kling 3.0 文生、首帧、首尾帧、原生音频和主体 ID 绑定。</p>
        <CodeBlock code={'export KLING_API_KEY="你的 API Key"\nexport KLING_BASE_URL="https://api-singapore.klingai.com"\nexport KLING_MODEL="kling-3.0"'} onCopied={() => notify("配置示例已复制")} />
        <p><a href="https://kling.ai/document-api/api/get-started/authentication" target="_blank" rel="noreferrer">官方鉴权文档</a> · <a href="https://kling.ai/document-api/api/get-started/kling-skills" target="_blank" rel="noreferrer">官方 Kling Skill（命令行）</a></p>
        <p>中国境内服务使用官方北京端点。API Key 与旧版 AK/SK 不通用，网页会员与 API 账单也应分别确认。</p>
      </section>
      <section className="panel onboarding-panel">
        <div className="panel-heading"><h3>Seedance 2.0 · 火山方舟 API</h3><a href="https://www.volcengine.com/docs/82379/1520757" target="_blank" rel="noreferrer">官方文档 <ExternalLink size={15} /></a></div>
        <p>使用火山方舟 API 额度。默认模型为 <code>doubao-seedance-2-0-260128</code>，通过 API Key 鉴权。</p>
        <ol className="step-list">
          <li><span>1</span><div><strong>创建方舟 API Key</strong><p>打开 <a href="https://ark.volcengine.com/region:cn-beijing/apikey" target="_blank" rel="noreferrer">API Key 管理</a>，创建或复制可访问视频模型的密钥。</p></div></li>
          <li><span>2</span><div><strong>填写本地后端配置</strong><p>在 <code>ai-drama-studio/.env.local</code> 的 <code>ARK_API_KEY=</code> 后粘贴密钥，然后重启后端。无需 Access Key / Secret Access Key。</p><CodeBlock code={'ARK_API_KEY=你的方舟API密钥\nARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3\nARK_VIDEO_MODEL=doubao-seedance-2-0-260128'} onCopied={() => notify("配置示例已复制")} /></div></li>
          <li><span>3</span><div><strong>刷新状态并选择 Seedance</strong><p>在镜头生成页选择 Seedance 2.0，设置 4–15 秒时长，确认素材和提示词后提交。</p></div></li>
        </ol>
      </section>
      <section className="panel"><div className="panel-heading"><h3>方舟连接状态</h3><button className="icon-button" disabled={loading} onClick={onRefresh} aria-label="刷新方舟状态"><RefreshCw size={16} /></button></div>
        <p>{account?.authenticated ? "任务查询鉴权已通过" : seedance?.configured ? "API Key 已加载，鉴权尚未通过" : "等待填写 ARK_API_KEY"}</p>
        <p>{account?.error || "刷新仅查询任务列表以核验鉴权，不提交视频生成任务。"}</p>
        <p>此状态不代表资源包余量或模型权限已核验，请在方舟控制台查看。</p>
      </section>
      <Callout>多模态参考支持本地图片和 MP3 / WAV 音频；参考视频需填写公网链接或 <code>asset://</code> 素材 ID。Seedance 2.0 不能仅用音频作为参考。已保存的旧 CLI 任务仍保留在历史中，其任务 ID 不能用于方舟查询。</Callout>
    </div>
  );
}
