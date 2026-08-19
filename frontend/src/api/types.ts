export interface HealthResponse {
  status: "ok";
  service: "ai-product-video-agent";
  api_version: "v1";
}

export interface CapabilityAvailability {
  available: boolean;
}

export interface CapabilitiesResponse {
  planning: {
    deepseek: CapabilityAvailability;
  };
  video: {
    minimax_hailuo: CapabilityAvailability;
    minimax_h3: CapabilityAvailability;
  };
  voice: {
    aliyun_tts: CapabilityAvailability;
    xfyun_tts: CapabilityAvailability;
  };
  ffmpeg: CapabilityAvailability;
}

export interface BackendErrorDetail {
  type: string;
  code: string;
  message: string;
  retryable: boolean;
  correlation_id: string;
}

export interface BackendErrorResponse {
  error: BackendErrorDetail;
}

export interface ApiResult<T> {
  data: T;
  correlationId: string | null;
}

export type TaskStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "INTERRUPTED"
  | "CANCELLED";

export type TaskOperation =
  | "CREATIVE_GENERATE"
  | "CREATIVE_RETRY"
  | "CREATIVE_REVISE"
  | "CREATIVE_REGENERATE"
  | "STORYBOARD_GENERATE"
  | "STORYBOARD_REVISE"
  | "STORYBOARD_REGENERATE"
  | "VIDEO_PROMPT_GENERATE"
  | "SHOT_GENERATE"
  | "ASSEMBLY"
  | "VOICE_GENERATE"
  | "SUBTITLE_GENERATE"
  | "FINAL_EXPORT";

export interface TaskError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface TaskResultReference {
  resource_type: string;
  resource_id: string | null;
  version: number | null;
}

export interface TaskRecord {
  task_id: string;
  project_id: string;
  operation: TaskOperation;
  status: TaskStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  correlation_id: string;
  error: TaskError | null;
  result: TaskResultReference | null;
}

export interface ProjectTaskListResponse {
  project_id: string;
  tasks: TaskRecord[];
}

export type WorkflowPhase =
  | "CREATIVE"
  | "CREATIVE_REVIEW"
  | "STORYBOARD"
  | "STORYBOARD_REVIEW"
  | "VIDEO_PROMPT"
  | "VIDEO_PROMPT_REVIEW"
  | "VIDEO_GENERATION"
  | "SHOT_REVIEW"
  | "ASSEMBLY"
  | "ASSEMBLY_REQUIRED"
  | "POST_PRODUCTION"
  | "FINAL_EXPORT"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "ERROR";

export interface AssemblyState {
  status: string;
  needs_update: boolean;
  version: number | null;
}

export interface StageState {
  status: string;
}

export interface ShotStageState extends StageState {
  approved: number;
  total: number;
}

export interface ComponentState extends StageState {
  version: number | null;
}

export interface FinalExportState extends ComponentState {
  created_at: string | null;
  stale: boolean;
}

export type AvailableAction =
  | "GENERATE_CREATIVE"
  | "RETRY_GENERATE_CREATIVE"
  | "APPROVE_CREATIVE"
  | "REVISE_CREATIVE"
  | "REGENERATE_CREATIVE"
  | "GENERATE_STORYBOARD"
  | "APPROVE_STORYBOARD"
  | "REVISE_STORYBOARD"
  | "REGENERATE_STORYBOARD"
  | "GENERATE_VIDEO_PROMPTS"
  | "APPROVE_VIDEO_PROMPTS"
  | "REVISE_VIDEO_PROMPTS"
  | "REGENERATE_VIDEO_PROMPTS"
  | "GENERATE_SHOTS"
  | "REVIEW_SHOTS"
  | "MANAGE_SHOT_VERSIONS"
  | "ASSEMBLE"
  | "GENERATE_VOICE"
  | "GENERATE_SUBTITLE"
  | "SET_MUSIC"
  | "FINAL_EXPORT";

export interface WorkflowStages {
  creative: StageState;
  storyboard: StageState;
  video_prompt: StageState;
  shots: ShotStageState;
  assembly: AssemblyState;
  voice: ComponentState;
  subtitle: ComponentState;
  music: ComponentState;
  export: FinalExportState;
}

export interface WorkflowState {
  workflow_phase: WorkflowPhase;
  status: string;
  stages: WorkflowStages;
  available_actions: AvailableAction[];
}

export interface ProjectRequest {
  product_name: string | null;
  product_description: string | null;
  user_notes: string | null;
  duration_seconds: number | null;
  video_style: string | null;
  video_purpose: string | null;
}

export interface PostProductionState {
  status: string;
  voice: ComponentState;
  subtitle: ComponentState;
  music: ComponentState;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  workflow_phase: WorkflowPhase;
  status: string;
  updated_at: string;
  assembly: AssemblyState;
  final_export: FinalExportState;
}

export interface ProjectListResponse {
  projects: ProjectSummary[];
}

export interface ProjectDetail {
  project_id: string;
  name: string;
  request: ProjectRequest;
  workflow: WorkflowState;
  assembly: AssemblyState;
  post_production: PostProductionState;
  final_export: FinalExportState;
  updated_at: string;
}

export interface ProjectWorkflowResponse extends WorkflowState {
  project_id: string;
  updated_at: string;
}

export interface CreativeNarrationPlan {
  enabled: boolean;
  tone: string | null;
  full_script: string | null;
  target_duration_seconds: number | null;
}

export interface CreativeSubtitleStrategy {
  enabled: boolean;
  tone: string | null;
  density: string | null;
  max_lines: number | null;
  preferred_position: string | null;
  principles: string[];
}

export interface CreativeGlobalConstraints {
  must: string[];
  must_not: string[];
}

export interface CreativeForbiddenWindow {
  start: number | null;
  end: number | null;
  tracks: string[];
}

export interface CreativePlanningContent {
  creative_concept: string | null;
  target_audience: string | null;
  key_message: string | null;
  visual_direction: string | null;
  narrative_arc: string | null;
  narration_plan: CreativeNarrationPlan;
  subtitle_strategy: CreativeSubtitleStrategy;
  global_constraints: CreativeGlobalConstraints;
  av_timeline_constraints: {
    forbidden_windows: CreativeForbiddenWindow[];
  };
}

export interface CreativeContentResponse {
  project_id: string;
  status: string;
  content: CreativePlanningContent | null;
}

export interface PlanningCue {
  text: string | null;
  start_offset: number | null;
  end_offset: number | null;
  position: string | null;
}

export interface StoryboardVideoConstraints {
  reserve_subtitle_space: boolean;
  subtitle_safe_area: string | null;
}

export interface StoryboardShotContent {
  shot_id: number | null;
  duration_seconds: number | null;
  purpose: string | null;
  visual: string | null;
  camera: string | null;
  voiceover_cues: PlanningCue[];
  subtitle_cues: PlanningCue[];
  video_constraints: StoryboardVideoConstraints;
}

export interface StoryboardPlanningContent {
  total_duration_seconds: number | null;
  shots: StoryboardShotContent[];
}

export interface StoryboardContentResponse {
  project_id: string;
  status: string;
  content: StoryboardPlanningContent | null;
}

export interface VideoPromptShotContent {
  shot_id: number | null;
  prompt_version: number | null;
  prompt_source: string | null;
  visual_prompt_core: string | null;
  prompt_text: string | null;
}

export interface VideoPromptsContentResponse {
  project_id: string;
  status: string;
  content: { shots: VideoPromptShotContent[] } | null;
}

export type ShotVersionRole = "OFFICIAL" | "PENDING_REVIEW" | "HISTORY";

export type ShotVisualInputMode =
  | "NONE"
  | "FIRST_FRAME"
  | "REFERENCE_ASSET"
  | "UNKNOWN";

export interface ShotSummary {
  shot_id: string;
  status: string;
  official_version: number | null;
  pending_review_version: number | null;
  version_count: number;
  generation_count: number;
}

export interface ShotListResponse {
  project_id: string;
  status: string;
  shots: ShotSummary[];
}

export interface ShotPromptSummary {
  version: number | null;
  source: string | null;
  visual_prompt_core: string | null;
  final_prompt: string | null;
}

export interface ShotGenerationSummary {
  model: string | null;
  visual_input_mode: ShotVisualInputMode;
}

export interface ShotVersion {
  version: number;
  role: ShotVersionRole;
  review_status: string;
  created_at: string | null;
  prompt: ShotPromptSummary;
  generation: ShotGenerationSummary;
  video_available: boolean;
}

export interface ShotDetail {
  project_id: string;
  shot_id: string;
  status: string;
  official_version: number | null;
  pending_review_version: number | null;
  version_count: number;
  generation_count: number;
  versions: ShotVersion[];
}

export type VoiceCalibrationStatus =
  | "PASS"
  | "WARNING"
  | "OUT_OF_TOLERANCE"
  | "OUT_OF_BOUNDS"
  | "NOT_APPLICABLE"
  | "UNKNOWN";

export interface AssemblyShotVersion {
  shot_id: number;
  video_version: number;
}

export interface AssemblyDetail {
  project_id: string;
  status: string;
  current_version: number | null;
  needs_update: boolean;
  changed_shot_id: number | null;
  created_at: string | null;
  total_duration: number | null;
  video_available: boolean;
  shots: AssemblyShotVersion[];
}

export interface VoiceDetail {
  project_id: string;
  status: string;
  version: number | null;
  created_at: string | null;
  script: string | null;
  script_source: string | null;
  model: string | null;
  voice: string | null;
  language: string | null;
  audio_available: boolean;
  planned_narration_duration: number | null;
  planned_first_voice_start: number | null;
  planned_last_voice_end: number | null;
  planned_voice_span: number | null;
  actual_audio_duration: number | null;
  voice_track_start: number | null;
  actual_voice_end: number | null;
  timing_mode: string | null;
  cue_level_alignment: boolean | null;
  script_matches_storyboard: boolean | null;
  calibration_status: VoiceCalibrationStatus;
}

export interface SubtitleCue {
  index: number;
  start: string;
  end: string;
  text: string;
}

export interface SubtitleDetail {
  project_id: string;
  status: string;
  version: number | null;
  source: string | null;
  timing_source: string | null;
  created_at: string | null;
  cue_count: number;
  content_available: boolean;
  cues: SubtitleCue[];
}

export interface MusicMixDetail {
  base_volume: number | null;
  ducking_enabled: boolean | null;
  ducking_ratio: number | null;
  duck_attack_seconds: number | null;
  duck_release_seconds: number | null;
  fade_in_seconds: number | null;
  fade_out_seconds: number | null;
  loop_music: boolean | null;
  ducking_status: string | null;
}

export interface MusicDetail {
  project_id: string;
  status: string;
  version: number | null;
  created_at: string | null;
  audio_available: boolean;
  format: string | null;
  duration_seconds: number | null;
  music_mix: MusicMixDetail | null;
}

export interface ExportVoiceTimingSummary {
  timing_mode: string | null;
  voice_track_start: number | null;
  actual_audio_duration: number | null;
  actual_voice_end: number | null;
  calibration_status: VoiceCalibrationStatus;
  cue_level_alignment: boolean | null;
}

export interface ExportDetail {
  project_id: string;
  status: string;
  version: number | null;
  created_at: string | null;
  stale: boolean;
  video_available: boolean;
  assembly_version: number | null;
  voice_version: number | null;
  subtitle_version: number | null;
  music_version: number | null;
  voice_timing: ExportVoiceTimingSummary | null;
  music_mix: MusicMixDetail | null;
}

export interface CreateProjectRequest {
  product_name: string;
  product_description: string;
  user_notes: string;
  duration_seconds: number;
  video_style: string;
  video_purpose: string;
}

export interface CreateProjectResponse {
  project_id: string;
  name: string;
  workflow_phase: WorkflowPhase;
  status: string;
  created_at: string;
  updated_at: string;
}
