import { Check, CircleHelp } from "lucide-react";
import type { PlatformStatus } from "./providerStatus";

export function ProviderStatusList({ platforms }: { platforms: PlatformStatus[] }) {
  return <div className="platform-status-list">{platforms.map(platform =>
    <article className="platform-status" key={platform.id}>
      <div className="platform-status-heading"><strong>{platform.name}</strong>
        <span className={platform.configured && platform.status !== "读取中" ? "platform-state configured" : "platform-state"}>
          {platform.configured && platform.status !== "读取中" ? <Check size={14} /> : <CircleHelp size={14} />}{platform.status}
        </span>
      </div>
      <p>{platform.description}</p><p>{platform.note}</p>
      <div className="platform-evidence">{platform.evidence}</div>
    </article>
  )}<p className="platform-status-footnote">生成证据仅来自工作台最近 100 条记录；配置、登录或历史成功均不保证当前额度与全部模型可用。</p></div>;
}
