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

export interface FinalExportState {
  status: string;
  version: number | null;
  created_at: string | null;
  stale: boolean;
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
