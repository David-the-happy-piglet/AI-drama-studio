import type { Generation, SeedanceAccount, VideoProvider } from "./types";

const platforms = [
  { id: "kling", name: "可灵 Kling", description: "官方 API · 3.0 / Turbo / Omni" },
  { id: "seedance", name: "Seedance", description: "火山方舟 API · Seedance 2.0" },
  { id: "minimax", name: "MiniMax", description: "Token Plan · Hailuo 2.3 · 补充素材" },
];

export function getProviderStatuses(
  providers: VideoProvider[], account: SeedanceAccount | null,
  generations: Generation[], loading = false,
) {
  return platforms.map(platform => {
    const provider = providers.find(item => item.provider === platform.id);
    const configured = Boolean(provider?.configured);
    const status = loading ? "读取中" : !provider ? "状态未知" : !configured ? "待配置"
      : platform.id === "seedance" ? account?.authenticated ? "API 已配置 · 鉴权通过"
        : !account || account.error ? "API 已配置 · 鉴权未确认" : "API 已配置 · 鉴权未通过"
      : "已配置";
    const successful = generations.filter(item => item.provider === platform.id
      && ["succeeded", "completed", "ready"].includes(item.status) && item.external_id && item.output_path).length;
    const note = loading ? "正在读取后端状态，不提交生成任务。"
      : !provider ? "未取得平台配置，请点击页面右上角刷新。"
      : !configured ? provider.configuration_issues.join("；") || "请在后端完成配置。"
      : platform.id === "seedance" ? account?.authenticated
        ? "任务查询鉴权通过；模型权限与资源包余量请在方舟控制台核对。"
        : account?.error || "请核对后端 ARK_API_KEY，重启后刷新状态。"
      : platform.id === "minimax" ? "订阅权益和今日剩余额度未核验，不自动切换付费 Key。"
      : "已加载后端密钥；当前鉴权有效性与余额未核验。";
    return { ...platform, configured, status, note,
      evidence: loading ? "生成记录读取中" : successful > 0
        ? `近期记录中有 ${successful} 条成功视频` : "近期记录未见成功视频，不能据此确认生成已验证",
    };
  });
}

export type PlatformStatus = ReturnType<typeof getProviderStatuses>[number];
