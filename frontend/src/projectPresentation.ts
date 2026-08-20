import type { AvailableAction, WorkflowPhase } from "./api/types";

export type StatusTone =
  | "neutral"
  | "progress"
  | "review"
  | "success"
  | "warning"
  | "danger";

export const WORKFLOW_PHASE_LABELS: Record<WorkflowPhase, string> = {
  CREATIVE: "创意策划",
  CREATIVE_REVIEW: "创意审核",
  STORYBOARD: "分镜规划",
  STORYBOARD_REVIEW: "分镜审核",
  VIDEO_PROMPT: "视频提示词",
  VIDEO_PROMPT_REVIEW: "提示词审核",
  VIDEO_GENERATION: "镜头生成",
  SHOT_REVIEW: "镜头审核",
  ASSEMBLY: "视频合片",
  ASSEMBLY_REQUIRED: "需要重新合片",
  POST_PRODUCTION: "后期制作",
  FINAL_EXPORT: "最终导出",
  COMPLETED: "已完成",
  FAILED: "执行失败",
  CANCELLED: "已取消",
  ERROR: "数据异常",
};

const STATUS_LABELS: Record<string, string> = {
  NOT_STARTED: "未开始",
  IN_PROGRESS: "进行中",
  RUNNING: "进行中",
  GENERATING: "生成中",
  READY: "已就绪",
  WAITING_REVIEW: "等待审核",
  APPROVED: "已审核",
  COMPLETED: "已完成",
  FINAL_COMPLETED: "已完成",
  FAILED: "执行失败",
  REJECTED: "已拒绝",
  CANCELLED: "已取消",
  STALE: "需要更新",
  UNREADABLE: "数据异常",
  UNKNOWN: "未知状态",
};

export const AVAILABLE_ACTION_LABELS: Record<AvailableAction, string> = {
  GENERATE_CREATIVE: "生成创意",
  RETRY_GENERATE_CREATIVE: "重新尝试生成",
  APPROVE_CREATIVE: "审核创意",
  REVISE_CREATIVE: "修改创意",
  REGENERATE_CREATIVE: "重新生成创意",
  GENERATE_STORYBOARD: "生成分镜",
  APPROVE_STORYBOARD: "审核分镜",
  REVISE_STORYBOARD: "修改分镜",
  REGENERATE_STORYBOARD: "重新生成分镜",
  GENERATE_VIDEO_PROMPTS: "生成视频提示词",
  APPROVE_VIDEO_PROMPTS: "审核视频提示词",
  REVISE_VIDEO_PROMPTS: "修改视频提示词",
  REGENERATE_VIDEO_PROMPTS: "重新生成视频提示词",
  GENERATE_SHOTS: "生成镜头",
  REVIEW_SHOTS: "审核镜头",
  MANAGE_SHOT_VERSIONS: "管理镜头版本",
  ASSEMBLE: "视频合片",
  GENERATE_VOICE: "生成配音",
  GENERATE_SUBTITLE: "生成字幕",
  SET_MUSIC: "设置音乐",
  FINAL_EXPORT: "最终导出",
};

export function statusPresentation(status: string): {
  label: string;
  tone: StatusTone;
} {
  const normalized = status.toUpperCase();
  const label = STATUS_LABELS[normalized] ?? "未知状态";
  if (["COMPLETED", "FINAL_COMPLETED", "APPROVED", "READY"].includes(normalized)) {
    return { label, tone: "success" };
  }
  if (["IN_PROGRESS", "RUNNING", "GENERATING"].includes(normalized)) {
    return { label, tone: "progress" };
  }
  if (normalized === "WAITING_REVIEW") {
    return { label, tone: "review" };
  }
  if (normalized === "STALE") {
    return { label, tone: "warning" };
  }
  if (["FAILED", "REJECTED", "CANCELLED", "UNREADABLE"].includes(normalized)) {
    return { label, tone: "danger" };
  }
  return { label, tone: "neutral" };
}

export function formatProjectDate(value: string | null | undefined): string {
  if (!value) {
    return "时间未知";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "时间未知";
  }
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}/${part("month")}/${part("day")} ${part("hour")}:${part("minute")}`;
}
