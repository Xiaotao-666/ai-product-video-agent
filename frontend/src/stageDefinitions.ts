import type {
  AvailableAction,
  ProjectWorkflowResponse,
} from "./api/types";
import { statusPresentation } from "./projectPresentation";

export const STAGE_DEFINITIONS = [
  {
    key: "creative",
    label: "创意策划",
    order: 1,
    description: "规划产品视频的核心创意方向与表达策略。",
    contentNote: "创意策划详细内容将在后续工作流阶段接入。",
  },
  {
    key: "storyboard",
    label: "分镜规划",
    order: 2,
    description: "将创意方向拆分为有序的镜头结构。",
    contentNote: "分镜规划详细内容将在后续工作流阶段接入。",
  },
  {
    key: "video-prompt",
    label: "视频提示词",
    order: 3,
    description: "为各镜头准备视频生成所需的提示词。",
    contentNote: "视频提示词正文将在后续工作流阶段接入。",
  },
  {
    key: "shots",
    label: "镜头",
    order: 4,
    description: "汇总镜头生成与审核进度。",
    contentNote: "镜头列表与媒体内容将在后续工作流阶段接入。",
  },
  {
    key: "assembly",
    label: "视频合片",
    order: 5,
    description: "汇总已审核镜头的合片状态与版本。",
    contentNote: "本阶段仅展示当前合片状态，不提供合片操作。",
  },
  {
    key: "voice",
    label: "配音",
    order: 6,
    description: "汇总项目配音的生成状态与版本。",
    contentNote: "本阶段仅展示配音状态，不播放或生成音频。",
  },
  {
    key: "subtitle",
    label: "字幕",
    order: 7,
    description: "汇总项目字幕的生成状态与版本。",
    contentNote: "本阶段仅展示字幕状态，不读取或编辑字幕文件。",
  },
  {
    key: "music",
    label: "音乐",
    order: 8,
    description: "汇总项目音乐的设置状态与版本。",
    contentNote: "本阶段仅展示音乐状态，不播放或调整音乐。",
  },
  {
    key: "export",
    label: "最终导出",
    order: 9,
    description: "汇总最终成片的导出状态与版本。",
    contentNote: "本阶段仅展示导出状态，不播放或重新导出视频。",
  },
] as const;

export type StageKey = (typeof STAGE_DEFINITIONS)[number]["key"];
export type StageDefinition = (typeof STAGE_DEFINITIONS)[number];

export interface StageFact {
  label: string;
  value: string;
}

export interface StagePresentation {
  status: string;
  summary: string;
  facts: StageFact[];
}

const STAGE_KEYS: ReadonlySet<string> = new Set(
  STAGE_DEFINITIONS.map((definition) => definition.key),
);

const STAGE_ACTIONS: Record<StageKey, ReadonlySet<AvailableAction>> = {
  creative: new Set([
    "GENERATE_CREATIVE",
    "APPROVE_CREATIVE",
    "REVISE_CREATIVE",
    "REGENERATE_CREATIVE",
  ]),
  storyboard: new Set([
    "GENERATE_STORYBOARD",
    "APPROVE_STORYBOARD",
    "REVISE_STORYBOARD",
    "REGENERATE_STORYBOARD",
  ]),
  "video-prompt": new Set([
    "GENERATE_VIDEO_PROMPTS",
    "APPROVE_VIDEO_PROMPTS",
    "REVISE_VIDEO_PROMPTS",
    "REGENERATE_VIDEO_PROMPTS",
  ]),
  shots: new Set([
    "GENERATE_SHOTS",
    "REVIEW_SHOTS",
    "MANAGE_SHOT_VERSIONS",
  ]),
  assembly: new Set(["ASSEMBLE"]),
  voice: new Set(["GENERATE_VOICE"]),
  subtitle: new Set(["GENERATE_SUBTITLE"]),
  music: new Set(["SET_MUSIC"]),
  export: new Set(["FINAL_EXPORT"]),
};

function versionValue(version: number | null): string {
  return version === null ? "未生成" : `v${version}`;
}

function versionSuffix(version: number | null): string {
  return version === null ? "" : ` · v${version}`;
}

export function isStageKey(value: string | undefined): value is StageKey {
  return typeof value === "string" && STAGE_KEYS.has(value);
}

export function getStageDefinition(
  key: string | undefined,
): StageDefinition | null {
  if (!isStageKey(key)) {
    return null;
  }
  return (
    STAGE_DEFINITIONS.find((definition) => definition.key === key) ?? null
  );
}

export function projectWorkspacePath(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}`;
}

export function projectStagePath(projectId: string, key: StageKey): string {
  return `${projectWorkspacePath(projectId)}/stages/${key}`;
}

export function actionsForStage(
  workflow: ProjectWorkflowResponse,
  key: StageKey,
): AvailableAction[] {
  return workflow.available_actions.filter((action) =>
    STAGE_ACTIONS[key].has(action),
  );
}

export function stagePresentation(
  workflow: ProjectWorkflowResponse,
  key: StageKey,
): StagePresentation {
  const { stages } = workflow;

  if (key === "shots") {
    const state = stages.shots;
    const status = statusPresentation(state.status).label;
    return {
      status: state.status,
      summary:
        state.total > 0
          ? `${state.approved} / ${state.total} 已审核`
          : status,
      facts: [
        { label: "当前状态", value: status },
        { label: "已审核镜头", value: `${state.approved} / ${state.total}` },
      ],
    };
  }

  if (key === "assembly") {
    const state = stages.assembly;
    const recordedStatus = statusPresentation(state.status).label;
    return {
      status: state.needs_update ? "STALE" : state.status,
      summary: state.needs_update
        ? `需要重新合片${versionSuffix(state.version)}`
        : `${recordedStatus}${versionSuffix(state.version)}`,
      facts: [
        { label: "当前状态", value: recordedStatus },
        { label: "Version", value: versionValue(state.version) },
        { label: "Needs Update", value: state.needs_update ? "是" : "否" },
      ],
    };
  }

  if (key === "export") {
    const state = stages.export;
    const recordedStatus = statusPresentation(state.status).label;
    return {
      status: state.stale ? "STALE" : state.status,
      summary: state.stale
        ? `需要重新导出${versionSuffix(state.version)}`
        : `${recordedStatus}${versionSuffix(state.version)}`,
      facts: [
        { label: "当前状态", value: recordedStatus },
        { label: "Version", value: versionValue(state.version) },
        { label: "Stale", value: state.stale ? "是" : "否" },
      ],
    };
  }

  if (key === "creative" || key === "storyboard" || key === "video-prompt") {
    const state =
      key === "creative"
        ? stages.creative
        : key === "storyboard"
          ? stages.storyboard
          : stages.video_prompt;
    const status = statusPresentation(state.status).label;
    return {
      status: state.status,
      summary: status,
      facts: [{ label: "当前状态", value: status }],
    };
  }

  const state =
    key === "voice"
      ? stages.voice
      : key === "subtitle"
        ? stages.subtitle
        : stages.music;
  const status = statusPresentation(state.status).label;
  return {
    status: state.status,
    summary: `${status}${versionSuffix(state.version)}`,
    facts: [
      { label: "当前状态", value: status },
      { label: "Version", value: versionValue(state.version) },
    ],
  };
}
