import { type ChangeEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getReferenceAssets,
  getReferenceImageUrl,
  uploadReferenceAsset,
} from "../api/client";
import type { ReferenceAsset } from "../api/types";


interface Props {
  projectId: string;
}

type LoadState = "loading" | "success" | "error";

function uploadErrorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) {
    return "参考素材上传失败，请稍后重试。";
  }
  switch (error.code) {
    case "INVALID_REFERENCE_FILE":
      return "所选文件为空或无效。";
    case "UNSUPPORTED_IMAGE_FORMAT":
      return "仅支持 JPG、JPEG、PNG 和 WebP 图片。";
    case "REFERENCE_IMAGE_INVALID":
      return "图片内容无法读取，请选择有效图片。";
    case "REFERENCE_FILE_TOO_LARGE":
      return "图片超过 20MB 大小限制。";
    case "PROJECT_BUSY":
      return "项目正在执行其他操作，请稍后重试。";
    case "NETWORK_ERROR":
      return "无法连接 Backend，请确认本地服务已启动。";
    default:
      return "参考素材上传失败，请稍后重试。";
  }
}

export function ReferenceAssetLibrary({ projectId }: Props) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [assets, setAssets] = useState<ReferenceAsset[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const uploadGuard = useRef(false);

  const loadAssets = useCallback(async () => {
    setLoadState("loading");
    setLoadError(null);
    try {
      const result = await getReferenceAssets(projectId);
      if (result.data.project_id !== projectId) {
        throw new ApiClientError({
          code: "INVALID_RESPONSE",
          message: "参考素材所属项目不一致。",
        });
      }
      setAssets(result.data.assets);
      setLoadState("success");
    } catch (error) {
      setLoadError(
        error instanceof ApiClientError && error.code === "NETWORK_ERROR"
          ? "无法连接 Backend，请确认本地服务已启动。"
          : "暂时无法读取项目素材，请稍后重试。",
      );
      setLoadState("error");
    }
  }, [projectId]);

  useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  async function handleFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0 || uploadGuard.current) return;

    uploadGuard.current = true;
    setUploading(true);
    setUploadError(null);
    setUploadNotice(null);
    setCorrelationId(null);
    let reused = 0;
    let completed = 0;
    let failed = 0;
    let lastError: ApiClientError | null = null;
    try {
      for (const [index, file] of files.entries()) {
        setUploadProgress(`正在上传参考素材 ${index + 1} / ${files.length}…`);
        try {
          const result = await uploadReferenceAsset(projectId, file);
          completed += 1;
          if (result.data.deduplicated) reused += 1;
        } catch (error) {
          failed += 1;
          if (error instanceof ApiClientError) lastError = error;
        }
      }
      await loadAssets();
      if (completed > 0) {
        setUploadNotice(
          reused === completed
            ? "成功处理的素材均已存在于项目中，未创建重复素材。"
            : reused > 0
              ? `已添加 ${completed - reused} 张素材，${reused} 张已存在。`
              : `已添加 ${completed} 张参考素材。`,
        );
      }
      if (failed > 0) {
        setUploadError(
          `${failed} 张参考素材上传失败。${lastError ? uploadErrorMessage(lastError) : "请稍后重试。"}`,
        );
        setCorrelationId(lastError?.correlationId ?? null);
      }
    } finally {
      uploadGuard.current = false;
      setUploading(false);
      setUploadProgress(null);
    }
  }

  return (
    <section className="workspace-section reference-library" aria-labelledby="reference-library-title">
      <div className="workspace-section-heading">
        <div>
          <p className="page-kicker">PROJECT ASSET LIBRARY</p>
          <h2 id="reference-library-title">项目素材</h2>
        </div>
        <p>项目级参考图可供后续 AI 理解与镜头生成复用；上传不会自动设为某个镜头的 Visual Input。</p>
      </div>

      <div className="reference-library-toolbar">
        <label className={`secondary-button reference-upload-button${uploading ? " is-disabled" : ""}`}>
          {uploading ? "正在添加…" : "+ 添加参考素材"}
          <input
            type="file"
            accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
            multiple
            disabled={uploading}
            onChange={(event) => void handleFiles(event)}
          />
        </label>
        <small>支持 JPG、JPEG、PNG、WebP，单张不超过 20MB。</small>
      </div>

      {uploadProgress && <p className="reference-library-status" role="status">{uploadProgress}</p>}
      {uploadNotice && <p className="reference-library-success" role="status">{uploadNotice}</p>}
      {uploadError && (
        <div className="reference-library-error" role="alert">
          <span>{uploadError}</span>
          {correlationId && <small>错误编号：{correlationId}</small>}
        </div>
      )}

      {loadState === "loading" && <p className="stage-empty-copy">正在读取项目素材…</p>}
      {loadState === "error" && (
        <div className="reference-library-error" role="alert">
          <span>{loadError}</span>
          <button className="text-button" type="button" onClick={() => void loadAssets()}>重试读取</button>
        </div>
      )}
      {loadState === "success" && assets.length === 0 && (
        <p className="stage-empty-copy">项目素材库尚为空。可以现在添加，也可以稍后再添加。</p>
      )}
      {loadState === "success" && assets.length > 0 && (
        <div className="reference-library-grid">
          {assets.map((asset) => (
            <article className="reference-library-card" key={asset.asset_id}>
              <img
                src={getReferenceImageUrl(projectId, asset.asset_id)}
                alt={`${asset.filename} 参考图预览`}
              />
              <div>
                <strong>{asset.filename}</strong>
                <span>{asset.asset_id}</span>
                <small>{asset.width} × {asset.height} · {asset.media_type}</small>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
