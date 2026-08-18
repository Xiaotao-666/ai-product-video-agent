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
