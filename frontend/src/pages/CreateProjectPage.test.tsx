import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  createProject,
  uploadReferenceAsset,
} from "../api/client";
import type {
  ApiResult,
  CreateProjectResponse,
} from "../api/types";
import { CreateProjectPage } from "./CreateProjectPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, createProject: vi.fn(), uploadReferenceAsset: vi.fn() };
});

const mockCreateProject = vi.mocked(createProject);
const mockUploadReferenceAsset = vi.mocked(uploadReferenceAsset);

const createdProject: CreateProjectResponse = {
  project_id: "project-new",
  name: "测试柠檬",
  workflow_phase: "CREATIVE",
  status: "NOT_STARTED",
  created_at: "2026-08-18T10:00:00+08:00",
  updated_at: "2026-08-18T10:00:00+08:00",
};

function successfulResult(
  data: CreateProjectResponse = createdProject,
): ApiResult<CreateProjectResponse> {
  return { data, correlationId: "req_create" };
}

function renderCreatePage() {
  return render(
    <MemoryRouter initialEntries={["/projects/new"]}>
      <Routes>
        <Route path="/projects/new" element={<CreateProjectPage />} />
        <Route path="/projects" element={<h1>Projects 测试目标</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

function fillValidForm() {
  fireEvent.change(screen.getByLabelText("产品名称 *"), {
    target: { value: "  测试柠檬  " },
  });
  fireEvent.change(screen.getByLabelText("产品描述 *"), {
    target: { value: "  新鲜柠檬饮料  " },
  });
  fireEvent.change(screen.getByLabelText("补充要求"), {
    target: { value: "  不要出现人物  " },
  });
  fireEvent.change(screen.getByLabelText("视频总时长 *"), {
    target: { value: "18" },
  });
  fireEvent.change(screen.getByLabelText("视觉风格 *"), {
    target: { value: "  清爽、年轻  " },
  });
  fireEvent.change(screen.getByLabelText("视频目的 *"), {
    target: { value: "  提升产品知名度  " },
  });
}

function submitForm() {
  fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
}

function safeError(code: string, correlationId = "req_error") {
  return new ApiClientError({
    message: "安全错误",
    status:
      code === "PROJECT_BUSY"
        ? 409
        : code === "PROJECT_CREATE_FAILED"
          ? 500
          : 422,
    code,
    correlationId,
    retryable: code === "PROJECT_BUSY",
  });
}

describe("CreateProjectPage", () => {
  beforeEach(() => {
    mockCreateProject.mockReset();
    mockUploadReferenceAsset.mockReset();
    mockCreateProject.mockResolvedValue(successfulResult());
    mockUploadReferenceAsset.mockResolvedValue({
      data: {
        asset_id: "ref_001",
        filename: "ref_001.png",
        media_type: "image/png",
        width: 1,
        height: 1,
        deduplicated: false,
      },
      correlationId: "req_reference",
    });
  });

  it("renders the create project page", () => {
    renderCreatePage();
    expect(
      screen.getByRole("heading", { name: "新建视频项目" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /返回 Projects/ })).toHaveAttribute(
      "href",
      "/projects",
    );
  });

  it("renders all six backend request fields", () => {
    renderCreatePage();
    expect(screen.getByLabelText("产品名称 *")).toBeInTheDocument();
    expect(screen.getByLabelText("产品描述 *")).toBeInTheDocument();
    expect(screen.getByLabelText("补充要求")).toBeInTheDocument();
    expect(screen.getByLabelText("视频总时长 *")).toHaveAttribute(
      "type",
      "number",
    );
    expect(screen.getByLabelText("视觉风格 *")).toBeInTheDocument();
    expect(screen.getByLabelText("视频目的 *")).toBeInTheDocument();
    expect(screen.getByLabelText("参考素材（可选）")).toHaveAttribute(
      "multiple",
    );
  });

  it("does not submit an empty product name", () => {
    renderCreatePage();
    fillValidForm();
    fireEvent.change(screen.getByLabelText("产品名称 *"), {
      target: { value: "   " },
    });
    submitForm();
    expect(screen.getByText("请输入产品名称。")).toBeInTheDocument();
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("does not request an invalid duration", () => {
    renderCreatePage();
    fillValidForm();
    fireEvent.change(screen.getByLabelText("视频总时长 *"), {
      target: { value: "0" },
    });
    submitForm();
    expect(screen.getByText("请输入大于 0 的整数秒数。")).toBeInTheDocument();
    expect(mockCreateProject).not.toHaveBeenCalled();
  });

  it("builds the exact trimmed CreateProjectRequest", async () => {
    renderCreatePage();
    fillValidForm();
    submitForm();
    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledTimes(1));
    expect(mockCreateProject).toHaveBeenCalledWith({
      product_name: "测试柠檬",
      product_description: "新鲜柠檬饮料",
      user_notes: "不要出现人物",
      duration_seconds: 18,
      video_style: "清爽、年轻",
      video_purpose: "提升产品知名度",
    });
  });

  it("calls createProject only once for one submit", async () => {
    renderCreatePage();
    fillValidForm();
    submitForm();
    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledTimes(1));
  });

  it("keeps reference upload optional and does not change project create JSON", async () => {
    renderCreatePage();
    fillValidForm();
    submitForm();
    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledTimes(1));
    expect(mockUploadReferenceAsset).not.toHaveBeenCalled();
  });

  it("creates once, then uploads multiple selected references in order", async () => {
    renderCreatePage();
    fillValidForm();
    const first = new File(["first"], "front.png", { type: "image/png" });
    const second = new File(["second"], "packaging.webp", { type: "image/webp" });
    fireEvent.change(screen.getByLabelText("参考素材（可选）"), {
      target: { files: [first, second] },
    });
    expect(screen.getByText("front.png")).toBeInTheDocument();
    expect(screen.getByText("packaging.webp")).toBeInTheDocument();
    submitForm();
    await waitFor(() => expect(mockUploadReferenceAsset).toHaveBeenCalledTimes(2));
    expect(mockCreateProject).toHaveBeenCalledTimes(1);
    expect(mockUploadReferenceAsset.mock.calls).toEqual([
      ["project-new", first],
      ["project-new", second],
    ]);
  });

  it("keeps the created project and retries only failed images", async () => {
    const failed = new ApiClientError({
      message: "safe",
      code: "REFERENCE_IMAGE_INVALID",
      correlationId: "req_upload_failed",
    });
    mockUploadReferenceAsset
      .mockResolvedValueOnce({
        data: {
          asset_id: "ref_001",
          filename: "ref_001.png",
          media_type: "image/png",
          width: 1,
          height: 1,
          deduplicated: false,
        },
        correlationId: "req_first",
      })
      .mockRejectedValueOnce(failed)
      .mockResolvedValueOnce({
        data: {
          asset_id: "ref_002",
          filename: "ref_002.png",
          media_type: "image/png",
          width: 1,
          height: 1,
          deduplicated: false,
        },
        correlationId: "req_retry",
      });
    renderCreatePage();
    fillValidForm();
    const first = new File(["first"], "front.png", { type: "image/png" });
    const second = new File(["second"], "bad.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("参考素材（可选）"), {
      target: { files: [first, second] },
    });
    submitForm();
    expect(await screen.findByText(/项目已创建，但 1 张参考素材上传失败/)).toBeInTheDocument();
    expect(mockCreateProject).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "进入项目" })).toHaveAttribute(
      "href",
      "/projects/project-new",
    );
    fireEvent.click(screen.getByRole("button", { name: "重试失败图片" }));
    await waitFor(() => expect(mockUploadReferenceAsset).toHaveBeenCalledTimes(3));
    expect(mockUploadReferenceAsset.mock.calls[2]).toEqual(["project-new", second]);
    expect(mockCreateProject).toHaveBeenCalledTimes(1);
  });

  it("disables the submit button while creating", async () => {
    mockCreateProject.mockReturnValue(
      new Promise<ApiResult<CreateProjectResponse>>(() => undefined),
    );
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByRole("button", { name: "创建中…" }),
    ).toBeDisabled();
  });

  it("blocks rapid double submission", async () => {
    mockCreateProject.mockReturnValue(
      new Promise<ApiResult<CreateProjectResponse>>(() => undefined),
    );
    renderCreatePage();
    fillValidForm();
    const form = screen.getByRole("button", { name: "创建项目" }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledTimes(1));
  });

  it("shows a safe success state after HTTP 201 data", async () => {
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(await screen.findByText("项目创建成功")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("project-new");
  });

  it("returns to /projects after success", async () => {
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByRole(
        "heading",
        { name: "Projects 测试目标" },
        { timeout: 1500 },
      ),
    ).toBeInTheDocument();
  });

  it("maps INVALID_VIDEO_DURATION to the duration field", async () => {
    mockCreateProject.mockRejectedValue(safeError("INVALID_VIDEO_DURATION"));
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("该视频时长暂不受支持，请调整后重试。"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("视频总时长 *")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("maps INVALID_PROJECT_NAME to the name field", async () => {
    mockCreateProject.mockRejectedValue(safeError("INVALID_PROJECT_NAME"));
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("产品名称无效，请检查后重试。"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("产品名称 *")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("shows a safe PROJECT_BUSY error", async () => {
    mockCreateProject.mockRejectedValue(safeError("PROJECT_BUSY"));
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("项目创建服务正忙，请稍后重试。"),
    ).toBeInTheDocument();
  });

  it("shows a safe PROJECT_CREATE_FAILED error", async () => {
    mockCreateProject.mockRejectedValue(safeError("PROJECT_CREATE_FAILED"));
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("项目创建失败，请稍后重试。"),
    ).toBeInTheDocument();
  });

  it("handles network failure without crashing", async () => {
    mockCreateProject.mockRejectedValue(
      new ApiClientError({
        message: "无法连接",
        code: "NETWORK_ERROR",
      }),
    );
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("无法连接 Backend，请确认本地服务已启动。"),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("fetch failed");
  });

  it("shows a safe correlation ID", async () => {
    mockCreateProject.mockRejectedValue(
      safeError("INVALID_PROJECT_NAME", "req_name_error"),
    );
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("错误编号：req_name_error"),
    ).toBeInTheDocument();
  });

  it("does not render absolute-path response extensions", async () => {
    const responseWithPath = Object.assign({}, createdProject, {
      local_path: "D:\\private\\project.json",
    });
    mockCreateProject.mockResolvedValue(successfulResult(responseWithPath));
    renderCreatePage();
    fillValidForm();
    submitForm();
    await screen.findByText("项目创建成功");
    expect(document.body).not.toHaveTextContent("D:\\private");
  });

  it("does not render secrets from unexpected failures", async () => {
    mockCreateProject.mockRejectedValue(
      new Error("API_KEY=hidden Provider Secret"),
    );
    renderCreatePage();
    fillValidForm();
    submitForm();
    expect(
      await screen.findByText("项目创建失败，请稍后重试。"),
    ).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("Provider Secret");
  });
});
