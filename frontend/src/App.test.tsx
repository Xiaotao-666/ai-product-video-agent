import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { getCapabilities, getHealth, getProjects } from "./api/client";
import type { CapabilitiesResponse } from "./api/types";

vi.mock("./api/client", () => ({
  getHealth: vi.fn(),
  getCapabilities: vi.fn(),
  getProjects: vi.fn(),
}));

const mockGetHealth = vi.mocked(getHealth);
const mockGetCapabilities = vi.mocked(getCapabilities);
const mockGetProjects = vi.mocked(getProjects);

function renderSystem() {
  return render(
    <MemoryRouter initialEntries={["/system"]}>
      <App />
    </MemoryRouter>,
  );
}

const capabilities: CapabilitiesResponse = {
  planning: { deepseek: { available: true } },
  video: {
    minimax_hailuo: { available: true },
    minimax_h3: { available: false },
  },
  voice: {
    aliyun_tts: { available: false },
    xfyun_tts: { available: true },
  },
  ffmpeg: { available: true },
};

describe("App", () => {
  beforeEach(() => {
    mockGetHealth.mockResolvedValue({
      data: {
        status: "ok",
        service: "ai-product-video-agent",
        api_version: "v1",
      },
      correlationId: "req_health",
    });
    mockGetCapabilities.mockResolvedValue({
      data: capabilities,
      correlationId: "req_capabilities",
    });
    mockGetProjects.mockResolvedValue({
      data: { projects: [] },
      correlationId: "req_projects",
    });
  });

  it("renders the application shell", () => {
    renderSystem();
    expect(
      screen.getByRole("heading", { name: "System Status" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
  });

  it("shows Connected and API version when health succeeds", async () => {
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
  });

  it("shows Offline without crashing when health fails", async () => {
    mockGetHealth.mockRejectedValue(new Error("network unavailable"));
    mockGetCapabilities.mockRejectedValue(new Error("network unavailable"));
    renderSystem();
    expect(await screen.findByText("Offline")).toBeInTheDocument();
    expect(screen.getByText("Backend 未连接")).toBeInTheDocument();
    expect(screen.getByText(/uvicorn web_backend\.app:app/)).toBeInTheDocument();
  });

  it("renders backend capability availability", async () => {
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getByText("MiniMax Hailuo")).toBeInTheDocument();
    expect(screen.getByText("MiniMax H3")).toBeInTheDocument();
    expect(screen.getByText("XFYUN TTS")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg")).toBeInTheDocument();
    expect(screen.getAllByText("Available")).toHaveLength(4);
    expect(screen.getAllByText("Unavailable")).toHaveLength(2);
  });

  it("never renders secret-bearing internal errors", async () => {
    mockGetHealth.mockRejectedValue(
      new Error("MINIMAX_API_KEY=must-not-render"),
    );
    mockGetCapabilities.mockRejectedValue(
      new Error("XFYUN_API_SECRET=must-not-render"),
    );
    renderSystem();
    await screen.findByText("Offline");
    expect(document.body).not.toHaveTextContent("must-not-render");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("API_SECRET");
  });

  it("keeps the page usable when capabilities fail", async () => {
    mockGetCapabilities.mockRejectedValue(new Error("invalid response"));
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("Unavailable")).toHaveLength(6);
    });
    expect(
      screen.getByRole("heading", { name: "Capabilities" }),
    ).toBeInTheDocument();
  });

  it("keeps the System Status route and active navigation", async () => {
    renderSystem();
    expect(
      screen.getByRole("heading", { name: "System Status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /System Status/ }),
    ).toHaveClass("nav-item-active");
    expect(screen.getByRole("link", { name: /Projects/ })).not.toHaveClass(
      "nav-item-active",
    );
  });

  it("routes the root path to Projects and marks navigation active", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: "Projects" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Projects/ })).toHaveClass(
      "nav-item-active",
    );
  });
});
