export interface PortraitJob {
  id: string;
  request_token: string;
  character_id: string;
  source_version: number;
  model: string;
  mode: "text" | "reference";
  effective_prompt: string;
  reference_path: string | null;
  reference_sha256: string | null;
  reference_snapshot_path: string | null;
  prompt: string;
  negative_prompt: string;
  count: number;
  resolution: string;
  aspect_ratio: string;
  task_id: string | null;
  status: string;
  error_message: string | null;
  created_at: string;
  candidates: { index: number; path: string | null; error: string | null }[];
}

export interface PortraitStudio {
  prompt: string;
  prompt_source: string;
  negative_prompt: string;
  reference: { path: string | null; sha256: string | null; error: string | null };
  reference_prompt: string;
  reference_prefix: string;
  character_version: number;
  configured: boolean;
  configuration_issues: string[];
  jobs: PortraitJob[];
}

export function portraitInputLabel(job: Pick<PortraitJob, "mode">) {
  return job.mode === "reference" ? "基于母版参考图 · Omni" : "纯文字生成 · 未使用母版图片";
}
export function referenceEffectivePrompt(prefix: string, prompt: string, negative: string) {
  return `${prefix}\n${prompt.trim()}${negative ? `\n避免：${negative}` : ""}`;
}
export const referencePresets: Record<string, string> = {
  front: "生成同一人物的正面头肩肖像，浅灰背景，柔和均匀棚光，中性表情，头肩完整入镜，保留参考图中的发型与服装。单人单幅，无拼图。",
  three_quarter: "生成参考人物的45度半侧面头肩肖像，脸朝画面左侧，双眼仍可见。浅灰背景，柔和均匀棚光，中性表情，保留原有发型与服装。单人单幅，无拼图。",
  profile: "生成参考人物的90度侧面头肩肖像，鼻尖朝画面左侧，清晰展示侧面鼻梁、下颌与耳部。浅灰背景，柔和均匀棚光，中性表情，保留原有发型与服装。单人单幅，无拼图。",
  full_body: "生成参考人物的正面全身照，从头到脚完整入镜，自然站立，双手放松垂下。浅灰背景，柔和均匀棚光，保留原有脸部特征、发型和可见服装。单人单幅，无拼图。",
};

export const portraitStatus: Record<string, string> = {
  submitting: "提交中", submitted: "排队中", processing: "生成中", unknown: "结果待核实",
  succeeded: "已完成", failed: "生成失败", partial: "部分图片已保存", download_failed: "待重试下载",
};
export function portraitPending(job: PortraitJob) {
  return ["submitting", "submitted", "processing", "unknown"].includes(job.status);
}
export function portraitNeedsPolling(job: PortraitJob) {
  return ["submitting", "submitted", "processing"].includes(job.status)
    || job.status === "succeeded" && job.candidates.some(item => !item.path);
}
export function mergePortraitJob(jobs: PortraitJob[], next: PortraitJob): PortraitJob[] {
  return [next, ...jobs.filter(item => item.id !== next.id)]
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}
