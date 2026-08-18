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
