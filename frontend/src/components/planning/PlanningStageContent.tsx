import { useCallback, useEffect, useState } from "react";

import {
  ApiClientError,
  getCreativeContent,
  getStoryboardContent,
  getVideoPrompts,
} from "../../api/client";
import type {
  CreativeContentResponse,
  PlanningCue,
  StoryboardContentResponse,
  VideoPromptsContentResponse,
} from "../../api/types";
import type { StageKey } from "../../stageDefinitions";


type PlanningStageKey = "creative" | "storyboard" | "video-prompt";
type PlanningResponse =
  | CreativeContentResponse
  | StoryboardContentResponse
  | VideoPromptsContentResponse;
type ContentState = "loading" | "success" | "error";

interface PlanningStageContentProps {
  projectId: string;
  stageKey: StageKey;
  creativeRefresh?: CreativeRefreshSnapshot | null;
}

export interface CreativeRefreshSnapshot {
  revision: number;
  response: CreativeContentResponse;
}

interface ContentError {
  code: string;
  correlationId: string | null;
}

const PROMPT_SOURCE_LABELS: Record<string, string> = {
  ai_generated: "AI 生成",
  ai_revision: "AI 修订",
  manual_edit: "手动编辑",
};

function isPlanningStageKey(key: StageKey): key is PlanningStageKey {
  return key === "creative" || key === "storyboard" || key === "video-prompt";
}

function displayText(value: string | null, fallback = "未记录"): string {
  return value && value.trim() ? value : fallback;
}

function formatSeconds(value: number | null): string {
  return value === null ? "未记录" : `${value}s`;
}

function formatTimeRange(cue: PlanningCue): string | null {
  if (cue.start_offset === null || cue.end_offset === null) {
    return null;
  }
  return `${cue.start_offset}s – ${cue.end_offset}s`;
}

function ContentHeading({ title }: { title: string }) {
  return (
    <div className="stage-section-heading planning-content-heading">
      <p className="page-kicker">PERSISTED PLANNING CONTENT</p>
      <h2 id="planning-content-title">{title}</h2>
    </div>
  );
}

function CreativeStageContent({ response }: { response: CreativeContentResponse }) {
  const content = response.content;
  if (!content) {
    return <p className="stage-empty-copy">创意策划尚未生成。</p>;
  }
  const narration = content.narration_plan;
  const subtitles = content.subtitle_strategy;
  return (
    <div className="planning-content-stack">
      <div className="planning-detail-grid">
        <article className="planning-detail-card">
          <h3>创意概述</h3>
          <p>{displayText(content.creative_concept)}</p>
        </article>
        <article className="planning-detail-card">
          <h3>目标受众</h3>
          <p>{displayText(content.target_audience)}</p>
        </article>
        <article className="planning-detail-card planning-detail-card-wide">
          <h3>核心信息</h3>
          <p>{displayText(content.key_message)}</p>
        </article>
        <article className="planning-detail-card planning-detail-card-wide">
          <h3>视觉方向</h3>
          <p>{displayText(content.visual_direction)}</p>
        </article>
        <article className="planning-detail-card planning-detail-card-wide">
          <h3>叙事结构</h3>
          <p>{displayText(content.narrative_arc)}</p>
        </article>
      </div>

      <article className="planning-detail-card planning-plan-card">
        <div className="planning-card-title-row">
          <h3>旁白规划</h3>
          <span>{narration.enabled ? "已启用" : "未启用"}</span>
        </div>
        {narration.enabled ? (
          <>
            <dl className="planning-inline-facts">
              <div><dt>语气</dt><dd>{displayText(narration.tone)}</dd></div>
              <div><dt>目标时长</dt><dd>{formatSeconds(narration.target_duration_seconds)}</dd></div>
            </dl>
            <p className="planning-long-text">{displayText(narration.full_script)}</p>
          </>
        ) : (
          <p>当前创意不启用旁白。</p>
        )}
      </article>

      <article className="planning-detail-card planning-plan-card">
        <div className="planning-card-title-row">
          <h3>字幕策略</h3>
          <span>{subtitles.enabled ? "已启用" : "未启用"}</span>
        </div>
        {subtitles.enabled ? (
          <>
            <dl className="planning-inline-facts">
              <div><dt>语气</dt><dd>{displayText(subtitles.tone)}</dd></div>
              <div><dt>密度</dt><dd>{displayText(subtitles.density)}</dd></div>
              <div><dt>最多行数</dt><dd>{subtitles.max_lines ?? "未记录"}</dd></div>
              <div><dt>首选位置</dt><dd>{displayText(subtitles.preferred_position)}</dd></div>
            </dl>
            {subtitles.principles.length > 0 && (
              <ul className="planning-bullet-list">
                {subtitles.principles.map((principle, index) => (
                  <li key={`${principle}-${index}`}>{principle}</li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p>当前创意不启用字幕。</p>
        )}
      </article>

      {(content.global_constraints.must.length > 0 ||
        content.global_constraints.must_not.length > 0) && (
        <article className="planning-detail-card planning-plan-card">
          <h3>全局约束</h3>
          <dl className="planning-inline-facts">
            <div>
              <dt>必须</dt>
              <dd>{content.global_constraints.must.join("；") || "无"}</dd>
            </div>
            <div>
              <dt>禁止</dt>
              <dd>{content.global_constraints.must_not.join("；") || "无"}</dd>
            </div>
          </dl>
        </article>
      )}
    </div>
  );
}

function CueList({ cues, emptyCopy }: { cues: PlanningCue[]; emptyCopy: string }) {
  if (cues.length === 0) {
    return <p className="planning-muted-copy">{emptyCopy}</p>;
  }
  return (
    <ul className="planning-cue-list">
      {cues.map((cue, index) => {
        const range = formatTimeRange(cue);
        return (
          <li key={`${cue.text ?? "cue"}-${index}`}>
            <div className="planning-cue-meta">
              {range && <span>{range}</span>}
              {cue.position && <span>{cue.position}</span>}
            </div>
            <p>{displayText(cue.text)}</p>
          </li>
        );
      })}
    </ul>
  );
}

function StoryboardStageContent({ response }: { response: StoryboardContentResponse }) {
  const content = response.content;
  if (!content) {
    return <p className="stage-empty-copy">分镜规划尚未生成。</p>;
  }
  return (
    <div className="planning-content-stack">
      <p className="planning-summary-line">
        总时长：{formatSeconds(content.total_duration_seconds)} · {content.shots.length} 个镜头
      </p>
      <div className="storyboard-shot-list">
        {content.shots.map((shot, index) => (
          <article className="storyboard-shot-card" key={`${shot.shot_id ?? "shot"}-${index}`}>
            <div className="planning-card-title-row">
              <h3>Shot {String(shot.shot_id ?? index + 1).padStart(2, "0")}</h3>
              <span>{formatSeconds(shot.duration_seconds)}</span>
            </div>
            <dl className="storyboard-shot-details">
              <div><dt>画面</dt><dd>{displayText(shot.visual)}</dd></div>
              <div><dt>镜头</dt><dd>{displayText(shot.camera)}</dd></div>
              <div><dt>目的</dt><dd>{displayText(shot.purpose)}</dd></div>
            </dl>
            <div className="storyboard-cue-grid">
              <div>
                <h4>旁白</h4>
                <CueList cues={shot.voiceover_cues} emptyCopy="无旁白 Cue" />
              </div>
              <div>
                <h4>字幕</h4>
                <CueList cues={shot.subtitle_cues} emptyCopy="无字幕 Cue" />
              </div>
            </div>
            <p className="storyboard-constraint">
              视频约束：{shot.video_constraints.reserve_subtitle_space ? "保留字幕安全区" : "不保留字幕安全区"}
              {shot.video_constraints.subtitle_safe_area
                ? ` · ${shot.video_constraints.subtitle_safe_area}`
                : ""}
            </p>
          </article>
        ))}
      </div>
    </div>
  );
}

function VideoPromptStageContent({ response }: { response: VideoPromptsContentResponse }) {
  const content = response.content;
  if (!content) {
    return <p className="stage-empty-copy">视频提示词尚未生成。</p>;
  }
  return (
    <div className="video-prompt-list">
      {content.shots.map((shot, index) => (
        <article className="video-prompt-card" key={`${shot.shot_id ?? "prompt"}-${index}`}>
          <div className="planning-card-title-row">
            <h3>Shot {String(shot.shot_id ?? index + 1).padStart(2, "0")}</h3>
            <span>
              {shot.prompt_version === null ? "Canonical Prompt" : `Prompt Version v${shot.prompt_version}`}
            </span>
          </div>
          {shot.prompt_source && (
            <p className="planning-prompt-source">
              来源：{PROMPT_SOURCE_LABELS[shot.prompt_source] ?? "历史正式版本"}
            </p>
          )}
          {shot.visual_prompt_core && (
            <div className="prompt-text-block">
              <h4>视觉 Prompt 核心</h4>
              <p>{shot.visual_prompt_core}</p>
            </div>
          )}
          {shot.prompt_text && (
            <div className="prompt-text-block prompt-text-block-final">
              <h4>最终视频 Prompt</h4>
              <p>{shot.prompt_text}</p>
            </div>
          )}
          {!shot.visual_prompt_core && !shot.prompt_text && (
            <p className="planning-muted-copy">该镜头暂未记录可展示的 Prompt。</p>
          )}
        </article>
      ))}
    </div>
  );
}

async function loadPlanningResponse(
  projectId: string,
  stageKey: PlanningStageKey,
): Promise<PlanningResponse> {
  if (stageKey === "creative") {
    return (await getCreativeContent(projectId)).data;
  }
  if (stageKey === "storyboard") {
    return (await getStoryboardContent(projectId)).data;
  }
  return (await getVideoPrompts(projectId)).data;
}

function contentTitle(stageKey: PlanningStageKey): string {
  if (stageKey === "creative") return "Creative 内容";
  if (stageKey === "storyboard") return "Storyboard 内容";
  return "视频提示词内容";
}

function loadingCopy(stageKey: PlanningStageKey): string {
  if (stageKey === "creative") return "正在加载创意内容…";
  if (stageKey === "storyboard") return "正在加载分镜内容…";
  return "正在加载视频提示词…";
}

export function PlanningStageContent({
  projectId,
  stageKey,
  creativeRefresh = null,
}: PlanningStageContentProps) {
  const planningStageKey = isPlanningStageKey(stageKey) ? stageKey : null;
  const [state, setState] = useState<ContentState>("loading");
  const [response, setResponse] = useState<PlanningResponse | null>(null);
  const [error, setError] = useState<ContentError | null>(null);

  const loadContent = useCallback(async () => {
    if (!planningStageKey) return;
    setState("loading");
    setResponse(null);
    setError(null);
    try {
      const result = await loadPlanningResponse(projectId, planningStageKey);
      if (result.project_id !== projectId) {
        throw new ApiClientError({
          message: "Planning response did not match project.",
          code: "INVALID_RESPONSE",
        });
      }
      setResponse(result);
      setState("success");
    } catch (caught) {
      setError({
        code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
        correlationId: caught instanceof ApiClientError ? caught.correlationId : null,
      });
      setState("error");
    }
  }, [planningStageKey, projectId]);

  useEffect(() => {
    if (planningStageKey) void loadContent();
  }, [loadContent, planningStageKey]);

  useEffect(() => {
    if (
      planningStageKey === "creative" &&
      creativeRefresh?.response.project_id === projectId
    ) {
      setResponse(creativeRefresh.response);
      setError(null);
      setState("success");
    }
  }, [creativeRefresh, planningStageKey, projectId]);

  if (!planningStageKey) return null;

  return (
    <section className="stage-section planning-content-section" aria-labelledby="planning-content-title">
      <ContentHeading title={contentTitle(planningStageKey)} />
      {state === "loading" && (
        <div className="planning-content-loading" aria-busy="true">
          <span aria-hidden="true" />
          <p>{loadingCopy(planningStageKey)}</p>
        </div>
      )}
      {state === "error" && (
        <div className="planning-content-error" role="alert">
          <h3>内容暂时无法读取</h3>
          <p>
            {error?.code === "NETWORK_ERROR"
              ? "无法连接本地 Backend，请确认服务已启动。"
              : "已保留阶段状态与导航，你可以单独重试内容读取。"}
          </p>
          {error?.correlationId && <small>错误编号：{error.correlationId}</small>}
          <button className="secondary-button" type="button" onClick={loadContent}>
            重试内容
          </button>
        </div>
      )}
      {state === "success" && response && planningStageKey === "creative" && (
        <CreativeStageContent response={response as CreativeContentResponse} />
      )}
      {state === "success" && response && planningStageKey === "storyboard" && (
        <StoryboardStageContent response={response as StoryboardContentResponse} />
      )}
      {state === "success" && response && planningStageKey === "video-prompt" && (
        <VideoPromptStageContent response={response as VideoPromptsContentResponse} />
      )}
    </section>
  );
}
