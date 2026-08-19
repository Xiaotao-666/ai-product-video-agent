import {
  type ChangeEvent,
  type FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useNavigate } from "react-router-dom";

import {
  ApiClientError,
  createProject,
  uploadReferenceAsset,
} from "../api/client";
import type { CreateProjectRequest } from "../api/types";

interface ProjectFormValues {
  product_name: string;
  product_description: string;
  user_notes: string;
  duration_seconds: string;
  video_style: string;
  video_purpose: string;
}

type ProjectFormErrors = Partial<Record<keyof ProjectFormValues, string>>;
type SubmissionState =
  | "idle"
  | "creating"
  | "uploading"
  | "partial-failure"
  | "success";

const INITIAL_VALUES: ProjectFormValues = {
  product_name: "",
  product_description: "",
  user_notes: "",
  duration_seconds: "",
  video_style: "",
  video_purpose: "",
};

function validateForm(values: ProjectFormValues): ProjectFormErrors {
  const errors: ProjectFormErrors = {};
  const duration = Number(values.duration_seconds);

  if (!values.product_name.trim()) {
    errors.product_name = "请输入产品名称。";
  }
  if (!values.product_description.trim()) {
    errors.product_description = "请输入产品描述。";
  }
  if (
    !values.duration_seconds.trim() ||
    !Number.isFinite(duration) ||
    !Number.isInteger(duration) ||
    duration <= 0
  ) {
    errors.duration_seconds = "请输入大于 0 的整数秒数。";
  }
  if (!values.video_style.trim()) {
    errors.video_style = "请输入视觉风格。";
  }
  if (!values.video_purpose.trim()) {
    errors.video_purpose = "请输入视频目的。";
  }
  return errors;
}

function globalErrorMessage(code: string): string {
  switch (code) {
    case "INVALID_PROJECT_REQUEST":
    case "INVALID_REQUEST":
      return "产品需求无效，请检查表单内容后重试。";
    case "PROJECT_BUSY":
      return "项目创建服务正忙，请稍后重试。";
    case "PROJECT_CREATE_FAILED":
      return "项目创建失败，请稍后重试。";
    case "NETWORK_ERROR":
      return "无法连接 Backend，请确认本地服务已启动。";
    default:
      return "项目创建失败，请稍后重试。";
  }
}

function safeFileLabel(file: File): string {
  return file.name.split(/[\\/]/).pop()?.slice(0, 160) || "未命名图片";
}

function referenceUploadErrorMessage(code: string): string {
  switch (code) {
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
      return "部分参考素材上传失败。";
  }
}

export function CreateProjectPage() {
  const navigate = useNavigate();
  const [values, setValues] = useState<ProjectFormValues>(INITIAL_VALUES);
  const [errors, setErrors] = useState<ProjectFormErrors>({});
  const [globalError, setGlobalError] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const [submissionState, setSubmissionState] =
    useState<SubmissionState>("idle");
  const [referenceFiles, setReferenceFiles] = useState<File[]>([]);
  const [failedReferenceFiles, setFailedReferenceFiles] = useState<File[]>([]);
  const [createdProjectId, setCreatedProjectId] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const submittingRef = useRef(false);
  const redirectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (redirectTimerRef.current !== null) {
        clearTimeout(redirectTimerRef.current);
      }
    },
    [],
  );

  const updateField =
    (field: keyof ProjectFormValues) =>
    (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const value = event.target.value;
      setValues((current) => ({ ...current, [field]: value }));
      setErrors((current) => ({ ...current, [field]: undefined }));
      setGlobalError(null);
      setCorrelationId(null);
    };

  const scheduleProjectsRedirect = () => {
    setSubmissionState("success");
    redirectTimerRef.current = setTimeout(() => {
      navigate("/projects", { replace: true });
    }, 350);
  };

  const uploadReferences = async (projectId: string, files: File[]) => {
    const failures: File[] = [];
    let lastError: ApiClientError | null = null;
    for (const [index, file] of files.entries()) {
      setUploadProgress(`正在上传参考素材 ${index + 1} / ${files.length}…`);
      try {
        await uploadReferenceAsset(projectId, file);
      } catch (error) {
        failures.push(file);
        if (error instanceof ApiClientError) lastError = error;
      }
    }
    setUploadProgress(null);
    setFailedReferenceFiles(failures);
    if (failures.length > 0) {
      setSubmissionState("partial-failure");
      setGlobalError(
        `项目已创建，但 ${failures.length} 张参考素材上传失败。${lastError ? referenceUploadErrorMessage(lastError.code) : ""}`,
      );
      setCorrelationId(lastError?.correlationId ?? null);
      submittingRef.current = false;
      return false;
    }
    scheduleProjectsRedirect();
    return true;
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) {
      return;
    }

    const validationErrors = validateForm(values);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      setGlobalError(null);
      setCorrelationId(null);
      return;
    }

    const request: CreateProjectRequest = {
      product_name: values.product_name.trim(),
      product_description: values.product_description.trim(),
      user_notes: values.user_notes.trim(),
      duration_seconds: Number(values.duration_seconds),
      video_style: values.video_style.trim(),
      video_purpose: values.video_purpose.trim(),
    };

    submittingRef.current = true;
    setSubmissionState("creating");
    setErrors({});
    setGlobalError(null);
    setCorrelationId(null);

    try {
      const created = await createProject(request);
      setCreatedProjectId(created.data.project_id);
      if (referenceFiles.length === 0) {
        scheduleProjectsRedirect();
      } else {
        setSubmissionState("uploading");
        await uploadReferences(created.data.project_id, referenceFiles);
      }
    } catch (error) {
      submittingRef.current = false;
      setSubmissionState("idle");
      if (error instanceof ApiClientError) {
        setCorrelationId(error.correlationId);
        if (error.code === "INVALID_PROJECT_NAME") {
          setErrors({ product_name: "产品名称无效，请检查后重试。" });
          return;
        }
        if (error.code === "INVALID_VIDEO_DURATION") {
          setErrors({
            duration_seconds: "该视频时长暂不受支持，请调整后重试。",
          });
          return;
        }
        setGlobalError(globalErrorMessage(error.code));
        return;
      }
      setGlobalError("项目创建失败，请稍后重试。");
    }
  };

  const retryFailedUploads = async () => {
    if (
      !createdProjectId ||
      failedReferenceFiles.length === 0 ||
      submittingRef.current
    ) {
      return;
    }
    submittingRef.current = true;
    setSubmissionState("uploading");
    setGlobalError(null);
    setCorrelationId(null);
    await uploadReferences(createdProjectId, failedReferenceFiles);
  };

  const busy = submissionState === "creating" || submissionState === "uploading";
  const projectAlreadyCreated = createdProjectId !== null;

  return (
    <main className="main-content create-project-page" aria-busy={busy}>
      <div className="create-page-content">
        <Link className="back-link" to="/projects">
          <span aria-hidden="true">←</span>
          返回 Projects
        </Link>

        <header className="create-page-header">
          <p className="page-kicker">NEW PROJECT</p>
          <h1>新建视频项目</h1>
          <p>
            填写产品信息和视频需求。项目创建后将进入创意策划阶段，但不会自动开始生成。
          </p>
        </header>

        <form className="project-form" onSubmit={handleSubmit} noValidate>
          <div className="form-heading">
            <div>
              <p className="card-eyebrow">PROJECT BRIEF</p>
              <h2>产品与视频需求</h2>
            </div>
            <span>带 * 的字段为必填项</span>
          </div>

          {submissionState === "success" && (
            <div className="form-success" role="status" aria-live="polite">
              <strong>项目创建成功</strong>
              <span>正在返回 Projects…</span>
            </div>
          )}

          {(submissionState === "creating" || submissionState === "uploading") && (
            <div className="form-success" role="status" aria-live="polite">
              <strong>
                {submissionState === "creating" ? "正在创建项目…" : "项目创建成功"}
              </strong>
              <span>{uploadProgress ?? "正在准备参考素材…"}</span>
            </div>
          )}

          {globalError && (
            <div className="form-global-error" role="alert">
              <strong>{globalError}</strong>
              {correlationId && <span>错误编号：{correlationId}</span>}
            </div>
          )}

          <div className="project-form-grid">
            <div className="form-field form-field-wide">
              <label htmlFor="product-name">产品名称 *</label>
              <input
                id="product-name"
                name="product_name"
                type="text"
                value={values.product_name}
                onChange={updateField("product_name")}
                maxLength={1000}
                required
                autoComplete="off"
                aria-invalid={Boolean(errors.product_name)}
                aria-describedby={
                  errors.product_name ? "product-name-error" : undefined
                }
              />
              {errors.product_name && (
                <span className="field-error" id="product-name-error">
                  {errors.product_name}
                </span>
              )}
            </div>

            <div className="form-field form-field-wide reference-file-field">
              <label htmlFor="reference-files">参考素材（可选）</label>
              <input
                id="reference-files"
                name="reference_files"
                type="file"
                accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                multiple
                disabled={busy || projectAlreadyCreated}
                onChange={(event) => {
                  setReferenceFiles(Array.from(event.target.files ?? []));
                  setFailedReferenceFiles([]);
                  setGlobalError(null);
                  setCorrelationId(null);
                }}
              />
              <span className="field-help">
                可上传产品、包装、品牌或角色参考图。素材将保存到项目素材库，可在后续 AI 理解和视频生成阶段复用。
              </span>
              {referenceFiles.length > 0 && (
                <ul className="selected-reference-files" aria-label="已选择参考素材">
                  {referenceFiles.map((file, index) => (
                    <li key={`${file.name}-${file.size}-${index}`}>
                      {safeFileLabel(file)}
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="form-field form-field-wide">
              <label htmlFor="product-description">产品描述 *</label>
              <textarea
                id="product-description"
                name="product_description"
                value={values.product_description}
                onChange={updateField("product_description")}
                maxLength={10000}
                rows={5}
                required
                placeholder="介绍产品特点、包装、核心卖点和目标受众。"
                aria-invalid={Boolean(errors.product_description)}
                aria-describedby={
                  errors.product_description
                    ? "product-description-error"
                    : undefined
                }
              />
              {errors.product_description && (
                <span className="field-error" id="product-description-error">
                  {errors.product_description}
                </span>
              )}
            </div>

            <div className="form-field form-field-wide">
              <label htmlFor="user-notes">补充要求</label>
              <textarea
                id="user-notes"
                name="user_notes"
                value={values.user_notes}
                onChange={updateField("user_notes")}
                maxLength={10000}
                rows={4}
                placeholder="不要出现人物；前 2 秒不要旁白；保持包装颜色和 Logo 不变。"
              />
            </div>

            <div className="form-field">
              <label htmlFor="duration-seconds">视频总时长 *</label>
              <div className="duration-input">
                <input
                  id="duration-seconds"
                  name="duration_seconds"
                  type="number"
                  value={values.duration_seconds}
                  onChange={updateField("duration_seconds")}
                  min={1}
                  step={1}
                  required
                  inputMode="numeric"
                  aria-invalid={Boolean(errors.duration_seconds)}
                  aria-describedby={
                    errors.duration_seconds
                      ? "duration-help duration-error"
                      : "duration-help"
                  }
                />
                <span aria-hidden="true">秒</span>
              </div>
              <span className="field-help" id="duration-help">
                最终可用时长由 Backend 和 Core 校验。
              </span>
              {errors.duration_seconds && (
                <span className="field-error" id="duration-error">
                  {errors.duration_seconds}
                </span>
              )}
            </div>

            <div className="form-field">
              <label htmlFor="video-style">视觉风格 *</label>
              <input
                id="video-style"
                name="video_style"
                type="text"
                value={values.video_style}
                onChange={updateField("video_style")}
                maxLength={2000}
                required
                placeholder="清爽、年轻、高明度、高饱和度"
                aria-invalid={Boolean(errors.video_style)}
                aria-describedby={
                  errors.video_style ? "video-style-error" : undefined
                }
              />
              {errors.video_style && (
                <span className="field-error" id="video-style-error">
                  {errors.video_style}
                </span>
              )}
            </div>

            <div className="form-field form-field-wide">
              <label htmlFor="video-purpose">视频目的 *</label>
              <input
                id="video-purpose"
                name="video_purpose"
                type="text"
                value={values.video_purpose}
                onChange={updateField("video_purpose")}
                maxLength={2000}
                required
                placeholder="提升产品知名度"
                aria-invalid={Boolean(errors.video_purpose)}
                aria-describedby={
                  errors.video_purpose ? "video-purpose-error" : undefined
                }
              />
              {errors.video_purpose && (
                <span className="field-error" id="video-purpose-error">
                  {errors.video_purpose}
                </span>
              )}
            </div>
          </div>

          {correlationId && !globalError && (
            <p className="form-correlation">错误编号：{correlationId}</p>
          )}

          <div className="form-actions">
            {submissionState === "partial-failure" && createdProjectId ? (
              <>
                <Link
                  className="secondary-button"
                  to={`/projects/${encodeURIComponent(createdProjectId)}`}
                >
                  进入项目
                </Link>
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => void retryFailedUploads()}
                >
                  重试失败图片
                </button>
              </>
            ) : (
              <>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => navigate("/projects")}
                  disabled={busy}
                >
                  取消
                </button>
                <button
                  className="primary-button"
                  type="submit"
                  disabled={busy || projectAlreadyCreated}
                >
                  {submissionState === "creating"
                    ? "创建中…"
                    : submissionState === "uploading"
                      ? "上传素材中…"
                      : submissionState === "success"
                        ? "创建成功"
                        : "创建项目"}
                </button>
              </>
            )}
          </div>
        </form>
      </div>
    </main>
  );
}
