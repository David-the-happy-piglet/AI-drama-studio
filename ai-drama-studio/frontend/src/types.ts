export interface Project {
  id: string;
  name: string;
  description: string;
  status: string;
  settings_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface Character {
  id: string;
  project_id: string;
  name: string;
  gender: string;
  apparent_age: string;
  ethnicity: string;
  height: string;
  body_type: string;
  face_description: string;
  hairstyle: string;
  hair_color: string;
  eye_characteristics: string;
  distinctive_features: string[];
  default_makeup: string;
  personality: string;
  speaking_style: string;
  default_voice_id: string | null;
  visual_style: string;
  negative_constraints: string[];
  master_reference_path: string | null;
  reference_assets: { additional_reference_paths: string[] } | null;
  provider_bindings: CharacterBinding[];
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface CharacterBinding {
  provider: string;
  source_version: number;
  external_task_id: string;
  task_id: string | null;
  element_id: string | null;
  status: string;
  error_message: string | null;
  updated_at: string;
}

export interface CharacterBindingState {
  binding: CharacterBinding | null;
  usable: boolean;
  issue: string | null;
  configured: boolean;
  configuration_issues: string[];
}

export interface CharacterBindingPreview {
  supported: boolean;
  elements: { character_id: string; name: string; id: string; element_id: string }[];
  errors: string[];
}

export interface Outfit {
  id: string;
  character_id: string;
  name: string;
  description: string;
  components: string[];
  color_palette: string[];
  reference_paths: string[];
  status: string;
}

export interface Episode {
  id: string;
  project_id: string;
  script_id: string | null;
  episode_number: number;
  title: string;
  duration_target: number;
  summary: string;
  status: string;
  version: number;
  created_at: string;
}

export interface Scene {
  id: string;
  episode_id: string;
  order_index: number;
  location_description: string;
  time_of_day: string;
  character_ids: string[];
  summary: string;
  dialogue: Array<Record<string, unknown>>;
  status: string;
  version: number;
  created_at: string;
}

export interface ShotCharacter {
  character_id: string;
  outfit_id?: string | null;
}

export interface Shot {
  id: string;
  scene_id: string;
  order_index: number;
  duration: number;
  characters: ShotCharacter[];
  shot_type: string;
  camera_angle: string;
  camera_movement: string;
  composition: string;
  action: string;
  emotion: string;
  dialogue: string;
  visual_description: string;
  continuity_requirements: string[];
  reference_requirements: string[];
  preferred_model: string | null;
  manual_provider_override: string | null;
  generation_prompt: string | null;
  negative_prompt: string | null;
  generation_mode: GenerationMode;
  direction?: ShotDirection;
  status: string;
  version: number;
  created_at: string;
}

export interface ShotDirection {
  screen_direction?: string;
  motion_source?: string[];
  frame_entry_exit?: string;
  expression_beat?: string;
  transition_out?: string;
  first_frame?: string;
  last_frame?: string;
  beats?: Array<{ start: number; end: number; text: string }>;
}

export interface LintFinding {
  ref: string;
  scene_index: number;
  shot_index: number;
  shot_id: string | null;
  rule: string;
  severity: "error" | "warning" | "info";
  message: string;
  suggestion: string;
}

export interface LintResult {
  summary: Record<string, number>;
  findings: LintFinding[];
}

export interface ReviewIssue {
  target: string;
  problem: string;
  suggestion: string;
  certainty: string;
  severity: string;
}

export interface ReviewReport {
  understood_story: string;
  one_line_theme: string;
  issues: ReviewIssue[];
  cannot_check: string[];
  verdict: "pass" | "fail";
}

export interface Review {
  id: string;
  stage: "story" | "storyboard" | "shot_pre_submit";
  target_id: string;
  target_version: number;
  verdict: "pass" | "fail";
  report: ReviewReport;
  model: string;
  created_at: string;
}

export interface CompiledPrompt {
  prompt: string;
  sections: Record<string, string>;
  notes: string[];
  current_prompt: string | null;
}

export interface ReviewGate {
  mode: "off" | "warn" | "required";
  verdict: "pass" | "fail" | "missing";
  review_id?: string;
  report?: ReviewReport | null;
}

export interface DirectorStatus {
  review_model: { configured: boolean; missing: string[]; model: string };
  review_gate: "off" | "warn" | "required";
}

export type GenerationMode =
  | "text_to_video"
  | "image_to_video"
  | "reference_to_video"
  | "first_last_frame"
  | "video_extension";

export interface Storyboard extends Episode {
  scenes: Array<Scene & { shots: Shot[] }>;
}

export interface VideoProvider {
  provider: string;
  configured: boolean;
  configuration_issues: string[];
  generation_modes: string[];
  supports_first_frame: boolean;
  supports_last_frame: boolean;
  supports_reference_images: boolean;
  supports_reference_videos: boolean;
  supports_reference_audio: boolean;
  supported_durations: number[];
  supported_aspect_ratios: string[];
  supported_resolutions: string[];
  metadata: Record<string, unknown>;
}

export interface Generation {
  review_warning?: string | null;
  id: string;
  request_token: string;
  project_id: string;
  episode_id: string;
  scene_id: string;
  shot_id: string;
  provider: string;
  model: string;
  prompt: string;
  negative_prompt: string;
  references: string[];
  status: string;
  duration: number | null;
  resolution: string | null;
  aspect_ratio: string | null;
  external_id: string | null;
  output_path: string | null;
  output_sha256: string | null;
  output_bytes: number | null;
  error_message: string | null;
  provider_response: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SeedanceAccount {
  authenticated: boolean;
  error?: string;
  account?: Record<string, unknown>;
}

export interface AssetUpload {
  path: string;
  url: string;
  filename: string;
  content_type: string | null;
  size: number;
  asset_kind: string;
}
