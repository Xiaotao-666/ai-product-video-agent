import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiClientError,
  getReferenceAssets,
  getReferenceImageUrl,
  getShotGenerationOptions,
  preflightShotGeneration,
} from "../../api/client";
import type {
  GenerationModelSelection,
  GenerationOptionsResponse,
  GenerationPreflightResponse,
  GenerationVisualInputMode,
  ReferenceAsset,
} from "../../api/types";
import { projectWorkspacePath } from "../../stageDefinitions";


interface Props {
  projectId: string;
  shotId: string;
}

type LoadState = "loading" | "success" | "error";

function loadErrorMessage(error: unknown): string {
  if (error instanceof ApiClientError && error.code === "NETWORK_ERROR") {
    return "无法连接 Backend，请确认本地服务已启动。";
  }
  return "暂时无法读取生成选项，请稍后重试。";
}

export function ShotGenerationPreparation({ projectId, shotId }: Props) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [options, setOptions] = useState<GenerationOptionsResponse | null>(null);
  const [assets, setAssets] = useState<ReferenceAsset[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selection, setSelection] =
    useState<GenerationModelSelection>("AUTO");
  const [requestedModel, setRequestedModel] = useState<string | null>(null);
  const [visualMode, setVisualMode] =
    useState<GenerationVisualInputMode>("none");
  const [assetId, setAssetId] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const submitGuard = useRef(false);
  const [result, setResult] = useState<GenerationPreflightResponse | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoadState("loading");
    setLoadError(null);
    Promise.all([
      getShotGenerationOptions(projectId, shotId),
      getReferenceAssets(projectId),
    ])
      .then(([optionsResult, referencesResult]) => {
        if (!active) return;
        if (
          optionsResult.data.project_id !== referencesResult.data.project_id ||
          optionsResult.data.project_id !== projectId
        ) {
          throw new ApiClientError({
            code: "INVALID_RESPONSE",
            message: "生成准备数据不一致。",
          });
        }
        setOptions(optionsResult.data);
        setAssets(referencesResult.data.assets);
        setLoadState("success");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(loadErrorMessage(error));
        setLoadState("error");
      });
    return () => {
      active = false;
    };
  }, [projectId, shotId]);

  const selectedAsset = useMemo(
    () => assets.find((asset) => asset.asset_id === assetId) ?? null,
    [assetId, assets],
  );
  const selectedModel = useMemo(
    () => options?.models.find((model) => model.model_id === requestedModel) ?? null,
    [options, requestedModel],
  );
  const manualCompatible =
    selectedModel?.supported_visual_input_modes.includes(visualMode) ?? true;

  function clearResult() {
    setResult(null);
    setSubmitError(null);
  }

  function changeSelection(value: GenerationModelSelection) {
    setSelection(value);
    if (value === "AUTO") {
      setRequestedModel(null);
    } else if (!requestedModel && options) {
      setRequestedModel(
        options.models.find((model) =>
          model.supported_visual_input_modes.includes(visualMode),
        )?.model_id ?? options.models[0]?.model_id ?? null,
      );
    }
    clearResult();
  }

  function changeVisualMode(value: GenerationVisualInputMode) {
    setVisualMode(value);
    setAssetId(null);
    clearResult();
  }

  async function checkConfiguration() {
    if (!options || checking || submitGuard.current || !options.eligible) return;
    submitGuard.current = true;
    setChecking(true);
    setSubmitError(null);
    setResult(null);
    try {
      const response = await preflightShotGeneration(projectId, shotId, {
        model_selection: selection,
        requested_model: selection === "MANUAL" ? requestedModel : null,
        visual_input: {
          mode: visualMode,
          asset_ids: visualMode === "none" || !assetId ? [] : [assetId],
        },
      });
      setResult(response.data);
    } catch (error) {
      setSubmitError(
        error instanceof ApiClientError
          ? `${error.message}${error.correlationId ? `（错误编号：${error.correlationId}）` : ""}`
          : "生成配置检查失败，请稍后重试。",
      );
    } finally {
      submitGuard.current = false;
      setChecking(false);
    }
  }

  return (
    <section
      className="shot-generation-preparation"
      aria-labelledby="generation-preparation-title"
    >
      <div className="stage-section-heading">
        <p className="page-kicker">GENERATION PREPARATION</p>
        <h2 id="generation-preparation-title">生成设置</h2>
        <p>先检查模型、Visual Input 和素材兼容性，本阶段不会提交视频生成。</p>
      </div>

      {loadState === "loading" && <p aria-live="polite">正在读取生成选项…</p>}
      {loadState === "error" && <p role="alert">{loadError}</p>}

      {loadState === "success" && options && (
        <>
          <dl className="generation-context-facts">
            <div><dt>镜头</dt><dd>{options.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
            <div><dt>将使用 Prompt</dt><dd>{options.shot.prompt_version ? `v${options.shot.prompt_version}` : "未就绪"}</dd></div>
            <div><dt>时长</dt><dd>{options.shot.duration_seconds} 秒</dd></div>
            <div><dt>分辨率</dt><dd>{options.shot.resolution}</dd></div>
          </dl>

          {!options.eligible && (
            <div className="generation-issues" role="status">
              <h3>当前尚不能检查初次生成配置</h3>
              <ul>{options.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul>
            </div>
          )}

          <fieldset className="generation-fieldset" disabled={!options.eligible || checking}>
            <legend>模型选择</legend>
            <label>
              <input
                type="radio"
                name="model-selection"
                checked={selection === "AUTO"}
                onChange={() => changeSelection("AUTO")}
              />
              自动
            </label>
            <label>
              <input
                type="radio"
                name="model-selection"
                checked={selection === "MANUAL"}
                onChange={() => changeSelection("MANUAL")}
              />
              手动
            </label>
            {selection === "MANUAL" && (
              <label className="generation-select-label">
                视频模型
                <select
                  aria-label="视频模型"
                  value={requestedModel ?? ""}
                  onChange={(event) => {
                    setRequestedModel(event.target.value || null);
                    clearResult();
                  }}
                >
                  {options.models.map((model) => {
                    const compatible = model.supported_visual_input_modes.includes(visualMode);
                    return (
                      <option key={model.model_id} value={model.model_id}>
                        {model.display_name}{!model.available ? " · 未配置" : ""}{!compatible ? " · 不兼容当前模式" : ""}
                      </option>
                    );
                  })}
                </select>
              </label>
            )}
            {selection === "MANUAL" && selectedModel && !manualCompatible && (
              <p className="generation-inline-warning" role="status">
                所选模型不支持当前 Visual Input；配置检查不会自动更换模型。
              </p>
            )}
          </fieldset>

          <fieldset className="generation-fieldset" disabled={!options.eligible || checking}>
            <legend>Visual Input</legend>
            <div className="visual-mode-options">
              {options.visual_input_modes.map((option) => (
                <label key={option.mode} className="visual-mode-option">
                  <span>
                    <input
                      type="radio"
                      name="visual-mode"
                      checked={visualMode === option.mode}
                      onChange={() => changeVisualMode(option.mode)}
                    />
                    {option.display_name}
                  </span>
                  <small>{option.description}</small>
                </label>
              ))}
            </div>
          </fieldset>

          {visualMode !== "none" && (
            <div className="reference-selector">
              <h3>选择项目已有素材</h3>
              {assets.length === 0 ? (
                <p className="stage-empty-copy">
                  当前项目暂无参考素材。可先
                  <Link to={projectWorkspacePath(projectId)}>前往项目素材库添加</Link>。
                </p>
              ) : (
                <div className="reference-grid">
                  {assets.map((asset) => (
                    <label key={asset.asset_id} className="reference-card">
                      <input
                        type="radio"
                        name="reference-asset"
                        checked={assetId === asset.asset_id}
                        disabled={!options.eligible || checking}
                        onChange={() => {
                          setAssetId(asset.asset_id);
                          clearResult();
                        }}
                      />
                      <img
                        src={getReferenceImageUrl(projectId, asset.asset_id)}
                        alt={`${asset.filename} 参考图预览`}
                      />
                      <span>{asset.filename}</span>
                      <small>{asset.width} × {asset.height}</small>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="generation-preflight-actions">
            <button
              className="primary-button"
              type="button"
              disabled={!options.eligible || checking}
              onClick={() => void checkConfiguration()}
            >
              {checking ? "正在检查生成配置…" : "检查生成配置"}
            </button>
            <p>生成视频将在下一阶段开放。</p>
          </div>

          <p className="paid-call-warning">
            真正生成视频会调用付费视频模型；本阶段尚未提交请求。
          </p>

          {submitError && <p className="generation-submit-error" role="alert">{submitError}</p>}

          {result && (
            <section
              className={`preflight-summary ${result.ready ? "preflight-summary-ready" : "preflight-summary-not-ready"}`}
              aria-label="生成前确认摘要"
            >
              <p className="page-kicker">PREFLIGHT</p>
              <h3>{result.ready ? "配置检查通过" : "配置尚未就绪"}</h3>
              <dl>
                <div><dt>镜头</dt><dd>{result.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
                <div><dt>Prompt</dt><dd>{result.shot.prompt_version ? `v${result.shot.prompt_version}` : "未就绪"}</dd></div>
                <div><dt>时长</dt><dd>{result.shot.duration_seconds} 秒</dd></div>
                <div><dt>Visual Input</dt><dd>{options.visual_input_modes.find((item) => item.mode === result.resolved?.visual_input_mode)?.display_name ?? visualMode}{selectedAsset ? ` · ${selectedAsset.asset_id}` : ""}</dd></div>
                <div><dt>模型</dt><dd>{result.resolved?.model_display_name ?? "未解析"}</dd></div>
                <div><dt>Provider</dt><dd>{result.resolved?.provider_display_name ?? "未解析"}</dd></div>
                <div><dt>API Version</dt><dd>{result.resolved?.api_version ?? "未解析"}</dd></div>
                <div><dt>生成模式</dt><dd>{result.resolved?.generation_mode_display_name ?? "未解析"}</dd></div>
              </dl>
              {result.issues.length > 0 && (
                <ul className="generation-issues-list">
                  {result.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}
                </ul>
              )}
              <p>本次仅完成本地配置检查，没有创建任务或调用视频模型。</p>
            </section>
          )}
        </>
      )}
    </section>
  );
}
