import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, setOfficialShotVersion } from "../../api/client";
import type { ShotDetail } from "../../api/types";
import { ShotSetOfficialAction } from "./ShotSetOfficialAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, setOfficialShotVersion: vi.fn() };
});

const mockSetOfficial = vi.mocked(setOfficialShotVersion);

const selectedShot: ShotDetail = {
  project_id: "project-a",
  shot_id: "shot_01",
  status: "APPROVED",
  official_version: 1,
  pending_review_version: null,
  version_count: 2,
  generation_count: 3,
  versions: [
    {
      version: 1,
      role: "OFFICIAL",
      review_status: "APPROVED",
      history_reason: null,
      created_at: null,
      prompt: { version: 1, source: "ai_generated", visual_prompt_core: null, final_prompt: "p1" },
      generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
      video_available: true,
    },
    {
      version: 3,
      role: "HISTORY",
      review_status: "APPROVED",
      history_reason: "PREVIOUSLY_APPROVED",
      created_at: null,
      prompt: { version: 2, source: "ai_revision", visual_prompt_core: null, final_prompt: "p2" },
      generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
      video_available: true,
    },
  ],
};

function renderAction(
  blockedReason: "PENDING_REVIEW" | "ACTIVE_GENERATION" | "INCOMPLETE_VERSION" | null = null,
  refresh = vi.fn().mockResolvedValue(undefined),
) {
  return {
    refresh,
    ...render(
      <ShotSetOfficialAction
        projectId="project-a"
        shotId="shot_01"
        version={1}
        promptVersion={1}
        currentOfficialVersion={3}
        blockedReason={blockedReason}
        onSelectedRefresh={refresh}
      />,
    ),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("ShotSetOfficialAction", () => {
  beforeEach(() => {
    mockSetOfficial.mockReset();
    mockSetOfficial.mockResolvedValue({ data: selectedShot, correlationId: "req_selected" });
  });

  it("opens a plain-language confirmation and cancel sends zero POSTs", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "设为正式版本" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("确认将 v1 设为当前正式版本？");
    expect(dialog).toHaveTextContent("当前正式版本v3");
    expect(dialog).toHaveTextContent("目标版本v1");
    expect(dialog).toHaveTextContent("目标 PromptPrompt v1");
    expect(dialog).toHaveTextContent("不会重新生成视频，也不会产生 MiniMax 费用");
    expect(dialog).toHaveTextContent("当前正式版本会完整保留在历史中");
    expect(dialog).toHaveTextContent("标记为需要重新合片");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockSetOfficial).not.toHaveBeenCalled();
  });

  it("guards rapid double confirmation and refreshes durable state", async () => {
    const pending = deferred<Awaited<ReturnType<typeof setOfficialShotVersion>>>();
    mockSetOfficial.mockReturnValue(pending.promise);
    const { refresh } = renderAction();
    fireEvent.click(screen.getByRole("button", { name: "设为正式版本" }));
    const confirm = screen.getByRole("button", { name: "确认设为正式版本" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockSetOfficial).toHaveBeenCalledTimes(1);
    expect(mockSetOfficial).toHaveBeenCalledWith("project-a", "shot_01", 1);
    expect(screen.getByRole("button", { name: "切换中…" })).toBeDisabled();
    await act(async () => {
      pending.resolve({ data: selectedShot, correlationId: "req_selected" });
      await pending.promise;
    });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["PENDING_REVIEW", "请先处理当前待审核新版本"],
    ["ACTIVE_GENERATION", "镜头生成任务进行中"],
    ["INCOMPLETE_VERSION", "历史版本文件不完整"],
  ] as const)("disables locally for %s", (reason, copy) => {
    renderAction(reason);
    expect(screen.getByRole("button", { name: "设为正式版本" })).toBeDisabled();
    expect(screen.getByText(new RegExp(copy))).toBeInTheDocument();
    expect(mockSetOfficial).not.toHaveBeenCalled();
  });

  it("renders only safe error copy and the correlation ID", async () => {
    mockSetOfficial.mockRejectedValue(
      new ApiClientError({
        code: "ACTION_NOT_ALLOWED",
        message: "D:\\private API_KEY provider raw response",
        correlationId: "req_safe_set_official",
      }),
    );
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "设为正式版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认设为正式版本" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("该历史版本当前不能设为正式版本");
    expect(screen.getByText("错误编号：req_safe_set_official")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("provider raw response");
  });
});
