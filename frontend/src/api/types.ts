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

export const TASK_STATUSES = [
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "INTERRUPTED",
  "CANCELLED",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];

export const ACTIVE_TASK_STATUSES = ["QUEUED", "RUNNING"] as const satisfies
  readonly TaskStatus[];

export const TERMINAL_TASK_STATUSES = [
  "SUCCEEDED",
  "FAILED",
  "INTERRUPTED",
  "CANCELLED",
] as const satisfies readonly TaskStatus[];

export const ERROR_TASK_STATUSES = ["FAILED", "INTERRUPTED"] as const satisfies
  readonly TaskStatus[];

export const TASK_OPERATIONS = [
  "CREATIVE_GENERATE",
  "CREATIVE_RETRY",
  "CREATIVE_REVISE",
  "CREATIVE_REGENERATE",
  "STORYBOARD_GENERATE",
  "STORYBOARD_REVISE",
  "STORYBOARD_REGENERATE",
  "VIDEO_PROMPT_GENERATE",
  "VIDEO_PROMPT_REVISE",
  "VIDEO_PROMPT_REGENERATE",
  "SHOT_GENERATE",
  "SHOT_REGENERATE",
  "SHOT_PROMPT_VERSION_GENERATE",
  "SHOT_RESUME",
  "SHOT_PROMPT_REVISION_DRAFT",
  "ASSEMBLY",
  "ASSEMBLY_EXECUTE",
  "VOICE_GENERATE",
  "SUBTITLE_GENERATE",
  "FINAL_EXPORT",
] as const;

export type TaskOperation = (typeof TASK_OPERATIONS)[number];

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
  target_id?: string | null;
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
  prompt_version?: number | null;
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
export type ShotVersionHistoryReason =
  | "PREVIOUSLY_APPROVED"
  | "SUPERSEDED"
  | "EXPLICITLY_REJECTED"
  | "UNKNOWN";

export type ShotVisualInputMode =
  | "NONE"
  | "FIRST_FRAME"
  | "REFERENCE_ASSET"
  | "UNKNOWN";

export interface ShotSummary {
  shot_id: string;
  order: number;
  title: string;
  status: string;
  prompt_status: string;
  video_status: string;
  review_status: string;
  official_version: number | null;
  pending_review_version: number | null;
  version_count: number;
  generation_count: number;
}

export interface ShotStatusAggregation {
  total: number;
  approved: number;
  waiting_review: number;
  generating: number;
  not_started: number;
  failed: number;
}

export interface ShotListResponse {
  project_id: string;
  status: string;
  aggregation: ShotStatusAggregation;
  shots: ShotSummary[];
}

export type MultiShotPlanStatus =
  | "READY"
  | "IN_PROGRESS"
  | "PARTIAL_PROGRESS"
  | "WAITING_REVIEW"
  | "COMPLETED"
  | "NOT_STARTED";

export interface MultiShotGenerationOption {
  shot_id: string;
  order: number;
  title: string;
  status: string;
  prompt_ready: boolean;
  video_status: string;
  available: boolean;
}

export interface MultiShotGenerationAggregation {
  total: number;
  queued: number;
  running: number;
  waiting_review: number;
  approved: number;
  failed: number;
  not_started: number;
}

export interface MultiShotGenerationOptionsResponse {
  project_id: string;
  status: MultiShotPlanStatus;
  max_parallel: number;
  aggregation: MultiShotGenerationAggregation;
  shots: MultiShotGenerationOption[];
}

export interface MultiShotGenerationStartRequest {
  shots: string[];
  confirm_paid_call: boolean;
}

export interface MultiShotGenerationPlanItem {
  shot_id: string;
  task_id: string;
  operation: "SHOT_GENERATE";
  status: TaskStatus;
}

export interface MultiShotGenerationPlanResponse {
  project_id: string;
  status: MultiShotPlanStatus;
  max_parallel: number;
  shots: MultiShotGenerationPlanItem[];
  aggregation: MultiShotGenerationAggregation;
}

export interface ShotPromptSummary {
  version: number | null;
  source: string | null;
  visual_prompt_core: string | null;
  final_prompt: string | null;
}

export interface ShotPromptVersionSummary {
  version: number;
  source: string | null;
  parent_version: number | null;
  created_at: string | null;
}

export interface ShotGenerationSummary {
  model: string | null;
  visual_input_mode: ShotVisualInputMode;
}

export interface ShotVersion {
  version: number;
  role: ShotVersionRole;
  review_status: string;
  history_reason?: ShotVersionHistoryReason | null;
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
  active_prompt_version?: number | null;
  approved_prompt_version?: number | null;
  prompt_versions?: ShotPromptVersionSummary[];
  versions: ShotVersion[];
}

export interface PromptRevisionDraftRequest {
  feedback: string;
}

export interface PromptRevisionDraftResponse {
  base_prompt_version: number;
  original_prompt: string;
  draft_prompt: string;
  feedback: string;
  created_at: string;
}

export interface PromptRevisionDraftAdoptResponse {
  project_id: string;
  shot_id: string;
  prompt_version: number;
  parent_version: number;
  source: "ai_revision";
  active_prompt_version: number;
  approved_prompt_version: number | null;
  created_at: string;
}

export type GenerationModelSelection = "AUTO" | "MANUAL";
export type GenerationVisualInputMode =
  | "none"
  | "reference_asset"
  | "first_frame";

export interface GenerationIssue {
  code: string;
  message: string;
}

export interface GenerationShotContext {
  shot_id: string;
  duration_seconds: number;
  prompt_version?: number | null;
  resolution: string;
  official_video_version?: number | null;
  pending_video_version?: number | null;
  next_video_version?: number | null;
  base_video_version?: number | null;
  next_prompt_version?: number | null;
  official_prompt_version?: number | null;
  prompt_source?: string | null;
  prompt_parent_version?: number | null;
}

export type GenerationIntent =
  | "INITIAL"
  | "REGENERATE_CURRENT_PROMPT"
  | "REGENERATE_MANUAL_PROMPT"
  | "GENERATE_WITH_PROMPT_VERSION";

export interface GenerationModelOption {
  model_id: string;
  display_name: string;
  provider: string;
  provider_display_name: string;
  api_version: string;
  available: boolean;
  supported_visual_input_modes: GenerationVisualInputMode[];
  supported_resolutions: string[];
  supported_durations: number[];
  min_duration: number | null;
  max_duration: number | null;
}

export interface GenerationVisualInputOption {
  mode: GenerationVisualInputMode;
  display_name: string;
  description: string;
  compatible_model_ids: string[];
}

export interface GenerationOptionsResponse {
  project_id: string;
  eligible: boolean;
  shot: GenerationShotContext;
  selection_modes: GenerationModelSelection[];
  visual_input_modes: GenerationVisualInputOption[];
  models: GenerationModelOption[];
  issues: GenerationIssue[];
  paid_call_required: boolean;
}

export interface ReferenceAsset {
  asset_id: string;
  filename: string;
  media_type: string;
  width: number;
  height: number;
}

export interface ReferenceAssetUploadResponse extends ReferenceAsset {
  deduplicated: boolean;
}

export interface ReferenceAssetListResponse {
  project_id: string;
  assets: ReferenceAsset[];
}

export interface GenerationPreflightRequest {
  intent?: GenerationIntent;
  model_selection: GenerationModelSelection;
  requested_model: string | null;
  visual_input: {
    mode: GenerationVisualInputMode;
    asset_ids: string[];
  };
  base_prompt_version?: number | null;
  edited_prompt?: string | null;
  target_prompt_version?: number | null;
}

export interface ResolvedGeneration {
  provider: string;
  provider_display_name: string;
  model: string;
  model_display_name: string;
  api_version: string;
  generation_mode: string;
  generation_mode_display_name: string;
  visual_input_mode: GenerationVisualInputMode;
  model_selection: GenerationModelSelection;
}

export interface GenerationPreflightResponse {
  ready: boolean;
  shot: GenerationShotContext;
  resolved: ResolvedGeneration | null;
  provider_available: boolean;
  selected_asset_ids: string[];
  issues: GenerationIssue[];
  warnings: GenerationIssue[];
  paid_call_required: boolean;
  preflight_fingerprint: string | null;
}

export interface GenerationStartRequest extends GenerationPreflightRequest {
  preflight_fingerprint: string;
  confirm_paid_call: boolean;
}

export type ShotGenerationState =
  | "NOT_STARTED"
  | "QUEUED"
  | "SUBMITTING"
  | "PROVIDER_RUNNING"
  | "READY_TO_DOWNLOAD"
  | "DOWNLOADING"
  | "LOCAL_FINALIZING"
  | "WAITING_REVIEW"
  | "APPROVED"
  | "FAILED"
  | "INTERRUPTED"
  | "SUBMISSION_UNKNOWN";

export type ShotGenerationResumeKind =
  | "POLL_EXISTING_TASK"
  | "DOWNLOAD_EXISTING_FILE"
  | "FINALIZE_LOCAL_VIDEO";

export interface ShotGenerationStatusResponse {
  project_id: string;
  shot_id: string;
  state: ShotGenerationState;
  resume_available: boolean;
  resume_kind: ShotGenerationResumeKind | null;
  video_version: number | null;
  prompt_version?: number | null;
  provider_submission_known: boolean;
  generation_intent?: GenerationIntent | null;
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

export interface AssemblyFinalVideoSource {
  shot_id: number;
  video_version: number;
  prompt_version: number | null;
  order: number | null;
}

export interface AssemblyFinalVideoVersion {
  final_video_version: number;
  assembly_version: number | null;
  created_at: string | null;
  total_duration: number | null;
  video_available: boolean;
  is_current: boolean;
  shots: AssemblyFinalVideoSource[];
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
  current_plan: AssemblyPlan | null;
  final_videos: AssemblyFinalVideoVersion[];
}

export type AssemblyPlanningStatus = "NOT_READY" | "READY" | "OUTDATED";

export interface AssemblyPlanShot {
  shot_id: number;
  order: number;
  approved_video_version: number;
  prompt_version: number;
  duration: number;
  resolution: string;
}

export interface AssemblyPlan {
  project_id: string;
  assembly_version: number;
  status: AssemblyPlanningStatus;
  created_at: string;
  total_duration: number;
  shots: AssemblyPlanShot[];
}

export interface AssemblyReadinessIssue {
  shot_id: number | null;
  order: number | null;
  reason: string;
}

export interface AssemblyReadiness {
  project_id: string;
  status: AssemblyPlanningStatus;
  ready: boolean;
  shot_count: number;
  ready_count: number;
  total_duration: number | null;
  shots: AssemblyPlanShot[];
  issues: AssemblyReadinessIssue[];
  current_plan: AssemblyPlan | null;
}

export interface VoiceDetail {
  project_id: string;
  status: string;
  version: number | null;
  created_at: string | null;
  script: string | null;
  script_source: string | null;
  provider: string | null;
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
  total_video_duration: number | null;
  duration_difference_seconds: number | null;
  duration_difference_ratio: number | null;
  timing_mode: string | null;
  cue_level_alignment: boolean | null;
  script_matches_storyboard: boolean | null;
  calibration_status: VoiceCalibrationStatus;
  timing_acceptance: VoiceTimingAcceptance | null;
}

export interface VoiceTimingAcceptance {
  accepted: boolean;
  accepted_at: string | null;
}

export type VoiceIntent = "GENERATE" | "REGENERATE";

export interface VoiceIssue {
  code: string;
  message: string;
}

export interface VoicePlannedTiming {
  first_start: number | null;
  last_end: number | null;
  span: number | null;
  narration_duration: number | null;
}

export interface VoiceScriptSummary {
  source: string;
  text: string;
  character_count: number;
  cue_count: number;
}

export interface VoiceProviderOption {
  provider_id: string;
  display_name: string;
  model: string;
  default_voice: string | null;
  language: string;
  supported_languages: string[];
  allowed_voices: string[];
  available: boolean;
}

export interface VoiceOptionsResponse {
  project_id: string;
  enabled: boolean;
  has_active_voice: boolean;
  active_version: number | null;
  next_version: number;
  script: VoiceScriptSummary | null;
  planned_timing: VoicePlannedTiming;
  providers: VoiceProviderOption[];
  default_provider: string | null;
  default_voice: string | null;
  default_language: string;
  manual_script_required: boolean;
}

export interface VoicePreflightRequest {
  intent: VoiceIntent;
  provider: string | null;
  voice: string;
  language: string;
  script_override: string | null;
}

export interface VoicePreflightResponse {
  project_id: string;
  ready: boolean;
  intent: VoiceIntent;
  next_voice_version: number;
  script: VoiceScriptSummary | null;
  provider: VoiceProviderOption | null;
  planned_timing: VoicePlannedTiming;
  issues: VoiceIssue[];
  warnings: VoiceIssue[];
  external_call_required: boolean;
  external_cost_possible: boolean;
  preflight_fingerprint: string | null;
}

export interface VoiceGenerateRequest extends VoicePreflightRequest {
  preflight_fingerprint: string;
  confirm_external_tts_call: boolean;
}

export interface VoiceVersionSummary {
  version: number;
  created_at: string | null;
  provider: string | null;
  model: string | null;
  voice: string | null;
  language: string | null;
  script_source: string | null;
  duration_seconds: number | null;
  calibration_status: VoiceCalibrationStatus;
  timing_acceptance: VoiceTimingAcceptance | null;
  audio_available: boolean;
  is_active: boolean;
}

export interface VoiceHistoryResponse {
  project_id: string;
  active_version: number | null;
  versions: VoiceVersionSummary[];
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
