import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { ApiClientError, getShots } from "../../api/client";
import type { ShotListResponse } from "../../api/types";
import { statusPresentation } from "../../projectPresentation";
import { projectShotPath, type StageKey } from "../../stageDefinitions";
import { StatusBadge } from "../StatusBadge";
import { MultiShotGenerationPanel } from "./MultiShotGenerationPanel";


type ContentState = "loading" | "success" | "error";

interface ShotsStageContentProps {
  projectId: string;
  stageKey: StageKey;
}

export function ShotsStageContent({
  projectId,
  stageKey,
}: ShotsStageContentProps) {
  const [state, setState] = useState<ContentState>("loading");
  const [response, setResponse] = useState<ShotListResponse | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);

  const loadShots = useCallback(async () => {
    if (stageKey !== "shots") {
      return;
    }
    setState("loading");
    setCorrelationId(null);
    try {
      const result = await getShots(projectId);
      setResponse(result.data);
      setState("success");
    } catch (error) {
      setResponse(null);
      setCorrelationId(
        error instanceof ApiClientError ? error.correlationId : null,
      );
      setState("error");
    }
  }, [projectId, stageKey]);

  useEffect(() => {
    void loadShots();
  }, [loadShots]);

  if (stageKey !== "shots") {
    return null;
  }

  return (
    <section
      className="stage-section shots-content-section"
      aria-labelledby="shots-content-title"
    >
      <div className="stage-section-heading">
        <p className="page-kicker">PERSISTED SHOT CONTENT</p>
        <h2 id="shots-content-title">镜头列表</h2>
      </div>

      {state === "loading" && (
        <div className="planning-content-loading" aria-live="polite">
          <span aria-hidden="true" />
          <p>正在加载镜头内容…</p>
        </div>
      )}

      {state === "error" && (
        <div className="planning-content-error" role="alert">
          <h3>镜头内容暂时无法读取</h3>
          <p>项目阶段摘要仍可使用，请单独重试镜头列表。</p>
          {correlationId && <small>错误编号：{correlationId}</small>}
          <button className="secondary-button" type="button" onClick={loadShots}>
            重试镜头内容
          </button>
        </div>
      )}

      {state === "success" && response && response.shots.length === 0 && (
        <p className="stage-empty-copy">当前项目尚无可浏览镜头。</p>
      )}

      {state === "success" && response && response.shots.length > 0 && (
        <>
          <MultiShotGenerationPanel
            projectId={projectId}
            onShotsChanged={loadShots}
          />
          <dl className="shot-collection-summary" aria-label="镜头状态汇总">
            <div><dt>镜头总数</dt><dd>{response.aggregation.total}</dd></div>
            <div><dt>已审核</dt><dd>{response.aggregation.approved}</dd></div>
            <div><dt>等待审核</dt><dd>{response.aggregation.waiting_review}</dd></div>
            <div><dt>生成中</dt><dd>{response.aggregation.generating}</dd></div>
            <div><dt>未开始</dt><dd>{response.aggregation.not_started}</dd></div>
            <div><dt>失败</dt><dd>{response.aggregation.failed}</dd></div>
          </dl>
          <div className="shot-summary-list">
            {response.shots.map((shot) => {
              const status = statusPresentation(shot.status);
              return (
                <article className="shot-summary-card" key={shot.shot_id}>
                  <div className="shot-summary-heading">
                    <div>
                      <p className="page-kicker">SHOT {shot.order}</p>
                      <h3>{shot.shot_id.replace("shot_", "Shot ")}</h3>
                      <p className="shot-summary-title">{shot.title}</p>
                    </div>
                    <StatusBadge label={status.label} tone={status.tone} />
                  </div>
                  <dl className="shot-summary-facts">
                    <div><dt>Prompt</dt><dd>{statusPresentation(shot.prompt_status).label}</dd></div>
                    <div><dt>Video</dt><dd>{statusPresentation(shot.video_status).label}</dd></div>
                    <div><dt>审核</dt><dd>{statusPresentation(shot.review_status).label}</dd></div>
                    <div>
                      <dt>当前正式</dt>
                      <dd>
                        {shot.official_version
                          ? `Video v${shot.official_version}`
                          : "尚无"}
                      </dd>
                    </div>
                    <div>
                      <dt>待审核新版本</dt>
                      <dd>
                        {shot.pending_review_version
                          ? `Video v${shot.pending_review_version}`
                          : "无"}
                      </dd>
                    </div>
                    <div><dt>累计生成</dt><dd>{shot.generation_count}</dd></div>
                  </dl>
                  <Link
                    className="secondary-button shot-detail-link"
                    to={projectShotPath(projectId, shot.shot_id)}
                  >
                    查看镜头
                  </Link>
                </article>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
