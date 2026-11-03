export interface KeyframeReference {
  character_id?: string; scene_asset_id?: string; kind?: "scene"; name: string; version: number; path: string | null;
  sha256: string | null; error: string | null; snapshot_path?: string;
}
export interface SelectedKeyframe {
  job_id: string; candidate_index: number; selection_token: string; path: string;
  sha256: string; shot_version: number; usable: boolean; error: string | null;
}
export interface KeyframeJob {
  id: string; shot_id: string; source_version: number; context_hash: string; request_token: string;
  model: string; prompt: string; negative_prompt: string; effective_prompt: string;
  references_snapshot: KeyframeReference[]; count: number; resolution: string; aspect_ratio: string;
  task_id: string | null; status: string; error_message: string | null; created_at: string;
  candidates: { index: number; path: string | null; error: string | null }[];
}
export interface KeyframeStudio {
  shot_version: number; context_hash: string; prompt: string; prompt_source: string;
  negative_prompt: string; reference_prefix: string; references: KeyframeReference[];
  errors: string[]; model: string; configured: boolean; configuration_issues: string[];
  jobs: KeyframeJob[]; selected: SelectedKeyframe | null;
}
export function keyframePrompt(prefix: string, prompt: string, negative: string, reference: boolean) {
  return reference ? `${prefix}\n${prompt.trim()}${negative ? `\n避免：${negative}` : ""}` : prompt.trim();
}
export function keyframePending(job: KeyframeJob) {
  return ["submitting", "submitted", "processing", "unknown"].includes(job.status);
}
export function keyframePollable(job: KeyframeJob) {
  return ["submitting", "submitted", "processing"].includes(job.status) || job.status === "succeeded" && job.candidates.some(c => !c.path);
}
export function selectedKeyframeToken(uploaded: boolean, selected: SelectedKeyframe | null, mode: string) {
  return !uploaded && selected?.usable && ["image_to_video", "first_last_frame"].includes(mode) ? selected.selection_token : null;
}
