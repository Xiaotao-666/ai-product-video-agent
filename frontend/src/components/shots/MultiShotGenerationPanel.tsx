import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { projectShotPath } from "../../stageDefinitions";

import {
  ApiClientError,
  getMultiShotGenerationOptions,
  startMultiShotGeneration,
} from "../../api/client";
import type { MultiShotGenerationOptionsResponse } from "../../api/types";
import { statusPresentation } from "../../projectPresentation";
import { StatusBadge } from "../StatusBadge";


interface MultiShotGenerationPanelProps {
  projectId: string;
  onShotsChanged: () => void;
}

type PanelState = "loading" | "ready" | "error";

export function MultiShotGenerationPanel({
  projectId,
  onShotsChanged,
}: MultiShotGenerationPanelProps) {
  const [state, setState] = useState<PanelState>("loading");
  const [options, setOptions] = useState<MultiShotGenerationOptionsResponse | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [correlationId, setCorrelationId] = useState<string | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setState("loading");
    try {
      const result = await getMultiShotGenerationOptions(projectId);
      setOptions(result.data);
      setSelected((current) => new Set(
        [...current].filter((shotId) =>
          result.data.shots.some((shot) => shot.shot_id === shotId && shot.available),
        ),
      ));
      setCorrelationId(null);
      setState("ready");
    } catch (error) {
      setCorrelationId(error instanceof ApiClientError ? error.correlationId : null);
      if (!quiet) setOptions(null);
      setState("error");
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const active = Boolean(
    options && (options.aggregation.queued > 0 || options.aggregation.running > 0),
  );

  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(() => {
      void load(true).then(onShotsChanged);
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [active, load, onShotsChanged, options]);

  const selectedCount = selected.size;
  const availableCount = useMemo(
    () => options?.shots.filter((shot) => shot.available).length ?? 0,
    [options],
  );

  const toggle = (shotId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(shotId)) next.delete(shotId);
      else next.add(shotId);
      return next;
    });
  };

  const submit = async () => {
    if (submitting || selectedCount === 0) return;
    if (!window.confirm(
      `确认生成已选择的 ${selectedCount} 个镜头？每个镜头会产生一次独立的付费视频生成调用。`,
    )) return;
    setSubmitting(true);
    setCorrelationId(null);
    try {
      await startMultiShotGeneration(projectId, {
        shots: [...selected],
        confirm_paid_call: true,
      });
      setSelected(new Set());
      await load(true);
      onShotsChanged();
    } catch (error) {
      setCorrelationId(error instanceof ApiClientError ? error.correlationId : null);
      setState("error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="multi-shot-generation-panel" aria-labelledby="multi-shot-title">
      <div className="stage-section-heading">
        <p className="page-kicker">MULTI-SHOT GENERATION</p>
        <h3 id="multi-shot-title">多镜头生成计划</h3>
        <p>每个镜头创建独立任务；并发上限由 Backend 统一控制。</p>
      </div>

      {state === "loading" && <p aria-live="polite">正在检查可生成镜头…</p>}
      {state === "error" && (
        <div className="planning-content-error" role="alert">
          <h4>多镜头生成状态暂时无法读取</h4>
          {correlationId && <small>错误编号：{correlationId}</small>}
          <button className="secondary-button" type="button" onClick={() => void load()}>
            重试生成状态
          </button>
        </div>
      )}

      {state === "ready" && options && (
        <>
          <div className="multi-shot-plan-heading">
            <StatusBadge
              label={statusPresentation(options.status).label}
              tone={statusPresentation(options.status).tone}
            />
            <span>最多同时生成 {options.max_parallel} 个镜头</span>
          </div>
          <dl className="multi-shot-progress" aria-label="项目镜头生成进度">
            <div><dt>总数</dt><dd>{options.aggregation.total}</dd></div>
            <div><dt>排队</dt><dd>{options.aggregation.queued}</dd></div>
            <div><dt>运行中</dt><dd>{options.aggregation.running}</dd></div>
            <div><dt>等待审核</dt><dd>{options.aggregation.waiting_review}</dd></div>
            <div><dt>已审核</dt><dd>{options.aggregation.approved}</dd></div>
            <div><dt>失败</dt><dd>{options.aggregation.failed}</dd></div>
          </dl>
          <div className="multi-shot-option-list">
            {options.shots.map((shot) => (
              <label className="multi-shot-option" key={shot.shot_id}>
                <input
                  type="checkbox"
                  checked={selected.has(shot.shot_id)}
                  disabled={!shot.available || submitting}
                  onChange={() => toggle(shot.shot_id)}
                />
                <span>
                  <strong>{shot.shot_id.replace("shot_", "Shot ")}</strong>
                  <small>{shot.title}</small>
                </span>
                <StatusBadge
                  label={shot.status === "FAILED" ? "生成失败" : statusPresentation(shot.status).label}
                  tone={statusPresentation(shot.status).tone}
                />
                {shot.status === "FAILED" && <Link to={projectShotPath(projectId, shot.shot_id)}>查看镜头 / 调整配置后重试</Link>}
              </label>
            ))}
          </div>
          <div className="multi-shot-submit-row">
            <p>
              可生成 {availableCount} 个 · 已选择 {selectedCount} 个
            </p>
            <button
              className="primary-button"
              type="button"
              disabled={selectedCount === 0 || submitting}
              onClick={() => void submit()}
            >
              {submitting ? "正在创建独立任务…" : "开始生成所选镜头"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
