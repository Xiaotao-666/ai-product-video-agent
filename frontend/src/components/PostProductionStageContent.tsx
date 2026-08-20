import { useCallback, useEffect, useState } from "react";

import {
  ApiClientError,
  createAssemblyPlan,
  getAssembly,
  getAssemblyReadiness,
  getAssemblyVideoUrl,
  getExport,
  getExportVideoUrl,
  getMusic,
  getMusicAudioUrl,
  getSubtitle,
  getVoice,
  getVoiceAudioUrl,
} from "../api/client";
import type {
  AssemblyDetail,
  AssemblyReadiness,
  ExportDetail,
  MusicDetail,
  MusicMixDetail,
  SubtitleDetail,
  VoiceCalibrationStatus,
  VoiceDetail,
} from "../api/types";
import { formatProjectDate, statusPresentation } from "../projectPresentation";
import type { StageKey } from "../stageDefinitions";
import { StatusBadge } from "./StatusBadge";


type DetailStageKey = "assembly" | "voice" | "subtitle" | "music" | "export";
type LoadState = "loading" | "success" | "error";

type LoadedDetail =
  | { kind: "assembly"; data: AssemblyDetail; readiness: AssemblyReadiness }
  | { kind: "voice"; data: VoiceDetail }
  | { kind: "subtitle"; data: SubtitleDetail }
  | { kind: "music"; data: MusicDetail }
  | { kind: "export"; data: ExportDetail };

interface DetailError {
  code: string;
  correlationId: string | null;
}

interface PostProductionStageContentProps {
  projectId: string;
  stageKey: StageKey;
}

const CALIBRATION_LABELS: Record<VoiceCalibrationStatus, string> = {
  PASS: "正常",
  WARNING: "有偏差",
  OUT_OF_TOLERANCE: "超出建议范围",
  OUT_OF_BOUNDS: "超出视频范围",
  NOT_APPLICABLE: "不适用",
  UNKNOWN: "未知",
};

const SOURCE_LABELS: Record<string, string> = {
  compiled_storyboard: "Storyboard Planned",
  storyboard_edited: "Storyboard Edited",
  manual: "Manual",
  voice_script: "Voice Script",
};

const ASSEMBLY_ISSUE_LABELS: Record<string, string> = {
  NO_SHOTS: "项目中没有可用于计划的镜头。",
  NOT_STARTED: "镜头尚未生成正式视频。",
  GENERATING: "镜头仍在生成。",
  WAITING_REVIEW: "镜头仍在等待审核。",
  FAILED: "镜头生成失败。",
  APPROVED_VERSION_MISSING: "镜头缺少正式视频版本。",
  APPROVED_INDEX_MISMATCH: "镜头正式版本索引不一致。",
  BUNDLE_INCOMPLETE: "正式视频版本 Bundle 不完整。",
  VIDEO_MISSING: "正式视频文件不可用。",
  INVALID_ORDER: "镜头顺序无效。",
};

function isDetailStage(stageKey: StageKey): stageKey is DetailStageKey {
  return ["assembly", "voice", "subtitle", "music", "export"].includes(
    stageKey,
  );
}

function versionLabel(version: number | null): string {
  return version === null ? "尚未生成" : `v${String(version).padStart(3, "0")}`;
}

function secondsLabel(value: number | null): string {
  return value === null ? "未记录" : `${value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}s`;
}

function percentLabel(value: number | null): string {
  return value === null ? "未设置" : `${Math.round(value * 100)}%`;
}

function booleanLabel(value: boolean | null): string {
  return value === null ? "未记录" : value ? "是" : "否";
}

function sourceLabel(value: string | null): string {
  if (!value) {
    return "未记录";
  }
  return SOURCE_LABELS[value] ?? value;
}

function dateLabel(value: string | null): string {
  return value ? formatProjectDate(value) : "未记录";
}

function errorCopy(code: string): string {
  if (code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动后重试。";
  }
  if (code.endsWith("_DATA_CORRUPT")) {
    return "该阶段的持久化数据暂时无法读取。";
  }
  if (code === "PROJECT_NOT_FOUND" || code === "INVALID_PROJECT_ID") {
    return "项目不存在或已被删除。";
  }
  return "暂时无法读取该阶段详情，请重试。";
}

function DetailHeading({ title }: { title: string }) {
  return (
    <div className="stage-section-heading postproduction-heading">
      <div>
        <p className="page-kicker">PERSISTED READ-ONLY DETAIL</p>
        <h2 id="postproduction-detail-title">{title}</h2>
      </div>
      <span>只读</span>
    </div>
  );
}

function StateFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function MediaUnavailable({ kind }: { kind: "视频" | "音频" }) {
  return <p className="media-unavailable">{kind}文件不可用</p>;
}

function MusicMix({ mix }: { mix: MusicMixDetail | null }) {
  if (!mix) {
    return <p className="postproduction-empty-copy">尚未保存 Music Mix 配置。</p>;
  }
  return (
    <dl className="postproduction-facts postproduction-mix-grid">
      <StateFact label="基础音量" value={percentLabel(mix.base_volume)} />
      <StateFact label="Ducking" value={booleanLabel(mix.ducking_enabled)} />
      <StateFact label="Ducking 比例" value={percentLabel(mix.ducking_ratio)} />
      <StateFact label="Attack" value={secondsLabel(mix.duck_attack_seconds)} />
      <StateFact label="Release" value={secondsLabel(mix.duck_release_seconds)} />
      <StateFact label="Fade In" value={secondsLabel(mix.fade_in_seconds)} />
      <StateFact label="Fade Out" value={secondsLabel(mix.fade_out_seconds)} />
      <StateFact label="Loop" value={booleanLabel(mix.loop_music)} />
      {mix.ducking_status && (
        <StateFact label="Ducking 状态" value={mix.ducking_status} />
      )}
    </dl>
  );
}

function AssemblyContent({
  projectId,
  detail,
  initialReadiness,
}: {
  projectId: string;
  detail: AssemblyDetail;
  initialReadiness: AssemblyReadiness;
}) {
  const [readiness, setReadiness] = useState(initialReadiness);
  const [creating, setCreating] = useState(false);
  const [planError, setPlanError] = useState<DetailError | null>(null);
  const [planCreated, setPlanCreated] = useState(false);
  useEffect(() => setReadiness(initialReadiness), [initialReadiness]);

  const createPlan = async () => {
    setCreating(true);
    setPlanError(null);
    setPlanCreated(false);
    try {
      const result = await createAssemblyPlan(projectId);
      setReadiness((current) => ({
        ...current,
        current_plan: result.data,
      }));
      setPlanCreated(true);
    } catch (error) {
      setPlanError({
        code: error instanceof ApiClientError ? error.code : "UNKNOWN_ERROR",
        correlationId: error instanceof ApiClientError ? error.correlationId : null,
      });
    } finally {
      setCreating(false);
    }
  };

  const status = statusPresentation(detail.needs_update ? "STALE" : detail.status);
  const planningStatus = statusPresentation(
    readiness.current_plan?.status ?? readiness.status,
  );
  const plannedShots = readiness.current_plan?.shots ?? readiness.shots;
  const plannedDuration = readiness.current_plan?.total_duration ?? readiness.total_duration;
  return (
    <>
      <DetailHeading title="合片详情" />
      <div className="postproduction-title-row">
        <h3>Assembly 计划</h3>
        <StatusBadge label={planningStatus.label} tone={planningStatus.tone} />
      </div>
      {readiness.current_plan?.status === "OUTDATED" && (
        <div className="stale-warning" role="status">
          <strong>当前镜头版本已变化，需要重新生成 Assembly 计划</strong>
          <span>旧计划保持不变，新计划会重新快照当前正式版本。</span>
        </div>
      )}
      <dl className="postproduction-facts">
        <StateFact
          label="计划版本"
          value={readiness.current_plan ? versionLabel(readiness.current_plan.assembly_version) : "尚未创建"}
        />
        <StateFact label="镜头数" value={String(readiness.shot_count)} />
        <StateFact label="已就绪" value={`${readiness.ready_count} / ${readiness.shot_count}`} />
        <StateFact label="计划总时长" value={secondsLabel(plannedDuration)} />
      </dl>
      {plannedShots.length > 0 && (
        <div className="postproduction-subsection">
          <h3>正式 Shot 版本快照</h3>
          <ul className="component-version-list assembly-plan-shot-list">
            {plannedShots.map((shot) => (
              <li key={`${shot.shot_id}-${shot.order}`}>
                <span>Shot {String(shot.shot_id).padStart(2, "0")}</span>
                <strong>
                  Video {versionLabel(shot.approved_video_version)} · Prompt {versionLabel(shot.prompt_version)} · {secondsLabel(shot.duration)} · {shot.resolution}
                </strong>
              </li>
            ))}
          </ul>
        </div>
      )}
      {readiness.issues.length > 0 && (
        <div className="postproduction-error" role="status">
          <p>以下镜头尚未满足 Assembly 计划条件：</p>
          <ul>
            {readiness.issues.map((issue, index) => (
              <li key={`${issue.shot_id ?? "project"}-${issue.reason}-${index}`}>
                {issue.shot_id ? `Shot ${String(issue.shot_id).padStart(2, "0")}：` : ""}
                {ASSEMBLY_ISSUE_LABELS[issue.reason] ?? "镜头状态暂不允许创建计划。"}
              </li>
            ))}
          </ul>
        </div>
      )}
      {readiness.ready && readiness.current_plan?.status !== "READY" && (
        <button
          className="primary-button"
          type="button"
          disabled={creating}
          onClick={() => void createPlan()}
        >
          {creating ? "正在创建计划…" : "创建 Assembly 计划"}
        </button>
      )}
      {planCreated && (
        <p className="action-success" role="status">
          Assembly 计划已创建。本阶段不会生成或拼接视频。
        </p>
      )}
      {planError && (
        <div className="postproduction-error" role="alert">
          <p>{errorCopy(planError.code)}</p>
          {planError.correlationId && <small>错误编号：{planError.correlationId}</small>}
        </div>
      )}
      <div className="postproduction-subsection">
      <div className="postproduction-title-row">
        <h3>当前正式合片</h3>
        <StatusBadge label={status.label} tone={status.tone} />
      </div>
      {detail.needs_update && (
        <div className="stale-warning" role="status">
          <strong>当前合片已过期，需要重新合片</strong>
          <span>旧版本仍可只读播放，本页面不会启动合片任务。</span>
        </div>
      )}
      {detail.current_version === null ? (
        <p className="postproduction-empty-copy">尚未生成合片。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <StateFact label="正式版本" value={versionLabel(detail.current_version)} />
            <StateFact label="状态" value={status.label} />
            <StateFact label="创建时间" value={dateLabel(detail.created_at)} />
            <StateFact label="总时长" value={secondsLabel(detail.total_duration)} />
            <StateFact
              label="Changed Shot"
              value={detail.changed_shot_id ? `Shot ${String(detail.changed_shot_id).padStart(2, "0")}` : "无"}
            />
          </dl>
          <div className="postproduction-media-card">
            <h3>合片视频</h3>
            {detail.video_available ? (
              <video controls preload="metadata" src={getAssemblyVideoUrl(projectId)} />
            ) : (
              <MediaUnavailable kind="视频" />
            )}
          </div>
          <div className="postproduction-subsection">
            <h3>使用的 Shot 版本</h3>
            {detail.shots.length > 0 ? (
              <ul className="component-version-list">
                {detail.shots.map((shot) => (
                  <li key={shot.shot_id}>
                    <span>Shot {String(shot.shot_id).padStart(2, "0")}</span>
                    <strong>{versionLabel(shot.video_version)}</strong>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="postproduction-empty-copy">未记录 Shot 版本摘要。</p>
            )}
          </div>
        </>
      )}
      </div>
    </>
  );
}

function VoiceContent({ projectId, detail }: { projectId: string; detail: VoiceDetail }) {
  const status = statusPresentation(detail.status);
  return (
    <>
      <DetailHeading title="配音详情" />
      <div className="postproduction-title-row">
        <h3>当前正式配音</h3>
        <StatusBadge label={status.label} tone={status.tone} />
      </div>
      {detail.version === null ? (
        <p className="postproduction-empty-copy">尚未生成配音。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <StateFact label="正式版本" value={versionLabel(detail.version)} />
            <StateFact label="脚本来源" value={sourceLabel(detail.script_source)} />
            <StateFact label="模型" value={detail.model ?? "未记录"} />
            <StateFact label="发音人" value={detail.voice ?? "未记录"} />
            <StateFact label="语言" value={detail.language ?? "未记录"} />
            <StateFact label="创建时间" value={dateLabel(detail.created_at)} />
          </dl>
          <div className="postproduction-script-card">
            <h3>配音脚本</h3>
            <p>{detail.script ?? "未保存脚本内容。"}</p>
          </div>
          <div className="postproduction-media-card">
            <h3>配音音频</h3>
            {detail.audio_available ? (
              <audio controls preload="metadata" src={getVoiceAudioUrl(projectId)} />
            ) : (
              <MediaUnavailable kind="音频" />
            )}
          </div>
          <div className="postproduction-subsection">
            <h3>Timeline / Calibration</h3>
            <dl className="postproduction-facts">
              <StateFact label="计划旁白时长" value={secondsLabel(detail.planned_narration_duration)} />
              <StateFact label="计划开始" value={secondsLabel(detail.planned_first_voice_start)} />
              <StateFact label="计划结束" value={secondsLabel(detail.planned_last_voice_end)} />
              <StateFact label="计划 Voice Span" value={secondsLabel(detail.planned_voice_span)} />
              <StateFact label="实际音频时长" value={secondsLabel(detail.actual_audio_duration)} />
              <StateFact label="实际轨道开始" value={secondsLabel(detail.voice_track_start)} />
              <StateFact label="实际结束" value={secondsLabel(detail.actual_voice_end)} />
              <StateFact label="Timing Mode" value={detail.timing_mode ?? "未记录"} />
              <StateFact label="Cue 对齐" value={booleanLabel(detail.cue_level_alignment)} />
              <StateFact label="脚本匹配 Storyboard" value={booleanLabel(detail.script_matches_storyboard)} />
              <StateFact label="校准状态" value={CALIBRATION_LABELS[detail.calibration_status]} />
            </dl>
          </div>
        </>
      )}
    </>
  );
}

function SubtitleContent({ detail }: { detail: SubtitleDetail }) {
  const status = statusPresentation(detail.status);
  return (
    <>
      <DetailHeading title="字幕详情" />
      <div className="postproduction-title-row">
        <h3>当前正式字幕</h3>
        <StatusBadge label={status.label} tone={status.tone} />
      </div>
      {detail.version === null ? (
        <p className="postproduction-empty-copy">尚未生成字幕。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <StateFact label="正式版本" value={versionLabel(detail.version)} />
            <StateFact label="来源" value={sourceLabel(detail.source)} />
            <StateFact label="Timing 来源" value={sourceLabel(detail.timing_source)} />
            <StateFact label="Cue 数量" value={String(detail.cue_count)} />
            <StateFact label="创建时间" value={dateLabel(detail.created_at)} />
          </dl>
          {detail.content_available ? (
            <ol className="subtitle-cue-list">
              {detail.cues.map((cue) => (
                <li key={`${cue.index}-${cue.start}`}>
                  <span>{cue.start} → {cue.end}</span>
                  <p>{cue.text}</p>
                </li>
              ))}
            </ol>
          ) : (
            <p className="media-unavailable">字幕文件不可用</p>
          )}
        </>
      )}
    </>
  );
}

function MusicContent({ projectId, detail }: { projectId: string; detail: MusicDetail }) {
  const status = statusPresentation(detail.status);
  return (
    <>
      <DetailHeading title="音乐详情" />
      <div className="postproduction-title-row">
        <h3>当前正式音乐</h3>
        <StatusBadge label={status.label} tone={status.tone} />
      </div>
      {detail.version === null ? (
        <p className="postproduction-empty-copy">尚未设置音乐。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <StateFact label="正式版本" value={versionLabel(detail.version)} />
            <StateFact label="格式" value={detail.format?.toUpperCase() ?? "未记录"} />
            <StateFact label="素材时长" value={secondsLabel(detail.duration_seconds)} />
            <StateFact label="创建时间" value={dateLabel(detail.created_at)} />
          </dl>
          <div className="postproduction-media-card">
            <h3>原始正式音乐</h3>
            {detail.audio_available ? (
              <audio controls preload="metadata" src={getMusicAudioUrl(projectId)} />
            ) : (
              <MediaUnavailable kind="音频" />
            )}
          </div>
          <div className="postproduction-subsection">
            <h3>Music Mix 配置</h3>
            <MusicMix mix={detail.music_mix} />
          </div>
        </>
      )}
    </>
  );
}

function ExportContent({ projectId, detail }: { projectId: string; detail: ExportDetail }) {
  const status = statusPresentation(detail.stale ? "STALE" : detail.status);
  return (
    <>
      <DetailHeading title="最终导出详情" />
      <div className="postproduction-title-row">
        <h3>当前最终成片</h3>
        <StatusBadge label={status.label} tone={status.tone} />
      </div>
      {detail.stale && (
        <div className="stale-warning" role="status">
          <strong>当前导出版本已过期</strong>
          <span>旧成片仍可只读播放，本页面不会重新执行导出。</span>
        </div>
      )}
      {detail.version === null ? (
        <p className="postproduction-empty-copy">尚未导出最终成片。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <StateFact label="正式版本" value={versionLabel(detail.version)} />
            <StateFact label="状态" value={status.label} />
            <StateFact label="创建时间" value={dateLabel(detail.created_at)} />
          </dl>
          <div className="postproduction-media-card">
            <h3>最终成片</h3>
            {detail.video_available ? (
              <video controls preload="metadata" src={getExportVideoUrl(projectId)} />
            ) : (
              <MediaUnavailable kind="视频" />
            )}
          </div>
          <div className="postproduction-subsection">
            <h3>使用的正式组件版本</h3>
            <ul className="component-version-list component-version-grid">
              <li><span>Assembly</span><strong>{versionLabel(detail.assembly_version)}</strong></li>
              <li><span>Voice</span><strong>{versionLabel(detail.voice_version)}</strong></li>
              <li><span>Subtitle</span><strong>{versionLabel(detail.subtitle_version)}</strong></li>
              <li><span>Music</span><strong>{versionLabel(detail.music_version)}</strong></li>
            </ul>
          </div>
          {detail.voice_timing && (
            <div className="postproduction-subsection">
              <h3>Voice Timing 摘要</h3>
              <dl className="postproduction-facts">
                <StateFact label="Timing Mode" value={detail.voice_timing.timing_mode ?? "未记录"} />
                <StateFact label="轨道开始" value={secondsLabel(detail.voice_timing.voice_track_start)} />
                <StateFact label="音频时长" value={secondsLabel(detail.voice_timing.actual_audio_duration)} />
                <StateFact label="实际结束" value={secondsLabel(detail.voice_timing.actual_voice_end)} />
                <StateFact label="校准状态" value={CALIBRATION_LABELS[detail.voice_timing.calibration_status]} />
                <StateFact label="Cue 对齐" value={booleanLabel(detail.voice_timing.cue_level_alignment)} />
              </dl>
            </div>
          )}
          <div className="postproduction-subsection">
            <h3>最终导出 Music Mix</h3>
            <MusicMix mix={detail.music_mix} />
          </div>
        </>
      )}
    </>
  );
}

export function PostProductionStageContent({
  projectId,
  stageKey,
}: PostProductionStageContentProps) {
  const [state, setState] = useState<LoadState>("loading");
  const [loaded, setLoaded] = useState<LoadedDetail | null>(null);
  const [loadError, setLoadError] = useState<DetailError | null>(null);

  const load = useCallback(async () => {
    if (!isDetailStage(stageKey)) {
      return;
    }
    setState("loading");
    setLoaded(null);
    setLoadError(null);
    try {
      if (stageKey === "assembly") {
        const [detail, readiness] = await Promise.all([
          getAssembly(projectId),
          getAssemblyReadiness(projectId),
        ]);
        setLoaded({ kind: "assembly", data: detail.data, readiness: readiness.data });
      } else if (stageKey === "voice") {
        setLoaded({ kind: "voice", data: (await getVoice(projectId)).data });
      } else if (stageKey === "subtitle") {
        setLoaded({ kind: "subtitle", data: (await getSubtitle(projectId)).data });
      } else if (stageKey === "music") {
        setLoaded({ kind: "music", data: (await getMusic(projectId)).data });
      } else {
        setLoaded({ kind: "export", data: (await getExport(projectId)).data });
      }
      setState("success");
    } catch (error) {
      setLoadError({
        code: error instanceof ApiClientError ? error.code : "UNKNOWN_ERROR",
        correlationId: error instanceof ApiClientError ? error.correlationId : null,
      });
      setState("error");
    }
  }, [projectId, stageKey]);

  useEffect(() => {
    if (isDetailStage(stageKey)) {
      void load();
    }
  }, [load, stageKey]);

  if (!isDetailStage(stageKey)) {
    return null;
  }

  if (state === "loading") {
    return (
      <section className="stage-section postproduction-detail-section" aria-busy="true" aria-label="正在加载阶段详情">
        <div className="postproduction-loading"><span /><p>正在加载已持久化详情…</p></div>
      </section>
    );
  }

  if (state === "error" || !loaded) {
    return (
      <section className="stage-section postproduction-detail-section" aria-labelledby="postproduction-detail-title">
        <DetailHeading title="详情暂不可用" />
        <div className="postproduction-error" role="alert">
          <p>{errorCopy(loadError?.code ?? "UNKNOWN_ERROR")}</p>
          {loadError?.correlationId && <small>错误编号：{loadError.correlationId}</small>}
          <button className="secondary-button" type="button" onClick={load}>重试</button>
        </div>
      </section>
    );
  }

  return (
    <section className="stage-section postproduction-detail-section" aria-labelledby="postproduction-detail-title">
      {loaded.kind === "assembly" && (
        <AssemblyContent
          projectId={projectId}
          detail={loaded.data}
          initialReadiness={loaded.readiness}
        />
      )}
      {loaded.kind === "voice" && <VoiceContent projectId={projectId} detail={loaded.data} />}
      {loaded.kind === "subtitle" && <SubtitleContent detail={loaded.data} />}
      {loaded.kind === "music" && <MusicContent projectId={projectId} detail={loaded.data} />}
      {loaded.kind === "export" && <ExportContent projectId={projectId} detail={loaded.data} />}
      <p className="stage-readonly-note">
        {loaded.kind === "assembly"
          ? "本阶段只保存版本化 Assembly 计划，不会运行 FFmpeg、生成合片视频或修改 Shot 版本。"
          : "本页只读取已保存内容，不会生成、编辑、切换或删除任何版本。"}
      </p>
    </section>
  );
}
