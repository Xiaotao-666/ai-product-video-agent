import { useEffect, useState } from "react";

import { getCapabilities, getHealth } from "../api/client";
import type { CapabilitiesResponse, HealthResponse } from "../api/types";
import { CapabilityGroup } from "../components/CapabilityGroup";

type ConnectionState = "loading" | "connected" | "offline";

export function SystemStatusPage() {
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("loading");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [capabilities, setCapabilities] =
    useState<CapabilitiesResponse | null>(null);

  useEffect(() => {
    let active = true;

    async function loadStatus() {
      const [healthResult, capabilityResult] = await Promise.allSettled([
        getHealth(),
        getCapabilities(),
      ]);
      if (!active) {
        return;
      }

      if (healthResult.status === "fulfilled") {
        setHealth(healthResult.value.data);
        setConnectionState("connected");
        setCapabilities(
          capabilityResult.status === "fulfilled"
            ? capabilityResult.value.data
            : null,
        );
      } else {
        setHealth(null);
        setCapabilities(null);
        setConnectionState("offline");
      }
    }

    void loadStatus();
    return () => {
      active = false;
    };
  }, []);

  const loading = connectionState === "loading";
  const connected = connectionState === "connected";

  return (
    <main className="main-content">
      <header className="page-header">
        <div>
          <p className="page-kicker">LOCAL WORKSPACE</p>
          <h1>System Status</h1>
        </div>
        <p className="page-summary">
          检查本地服务与生成能力是否已准备就绪。
        </p>
      </header>

      <section className="status-grid" aria-live="polite">
        <article className="backend-card">
          <div className="backend-card-heading">
            <div>
              <p className="card-eyebrow">BACKEND</p>
              <h2>Local API</h2>
            </div>
            <span
              className={`connection-badge connection-${connectionState}`}
            >
              <span className="status-dot" aria-hidden="true" />
              {loading ? "Connecting" : connected ? "Connected" : "Offline"}
            </span>
          </div>

          <div className="backend-facts">
            <div>
              <span className="fact-label">Service</span>
              <strong>
                {loading
                  ? "正在连接 Backend..."
                  : connected
                    ? "AI Product Video Agent"
                    : "Backend 未连接"}
              </strong>
            </div>
            <div>
              <span className="fact-label">API Version</span>
              <strong>{health?.api_version ?? "—"}</strong>
            </div>
          </div>

          {connectionState === "offline" && (
            <div className="offline-guidance" role="status">
              <p>请启动本地服务：</p>
              <code>
                .\.venv\Scripts\python.exe -m uvicorn web_backend.app:app
                --host 127.0.0.1 --port 8000
              </code>
            </div>
          )}
        </article>

        <aside className="phase-note">
          <span className="phase-number">2A</span>
          <div>
            <p className="card-eyebrow">CURRENT PHASE</p>
            <h2>Foundation connected</h2>
            <p>本阶段仅验证 Web 与 Backend 的安全只读连接。</p>
          </div>
        </aside>
      </section>

      <section className="capabilities-section">
        <div className="section-heading">
          <div>
            <p className="page-kicker">READINESS</p>
            <h2>Capabilities</h2>
          </div>
          <p>仅显示可用状态，不读取或展示任何凭据信息。</p>
        </div>

        <div className="capability-grid">
          <CapabilityGroup
            eyebrow="PLANNING"
            title="Reasoning"
            loading={loading}
            entries={[
              {
                label: "DeepSeek",
                available: capabilities?.planning.deepseek.available,
              },
            ]}
          />
          <CapabilityGroup
            eyebrow="VIDEO"
            title="Generation"
            loading={loading}
            entries={[
              {
                label: "MiniMax Hailuo",
                available: capabilities?.video.minimax_hailuo.available,
              },
              {
                label: "MiniMax H3",
                available: capabilities?.video.minimax_h3.available,
              },
            ]}
          />
          <CapabilityGroup
            eyebrow="VOICE"
            title="Speech"
            loading={loading}
            entries={[
              {
                label: "Aliyun TTS",
                available: capabilities?.voice.aliyun_tts.available,
              },
              {
                label: "XFYUN TTS",
                available: capabilities?.voice.xfyun_tts.available,
              },
            ]}
          />
          <CapabilityGroup
            eyebrow="MEDIA"
            title="Local Tools"
            loading={loading}
            entries={[
              {
                label: "FFmpeg",
                available: capabilities?.ffmpeg.available,
              },
            ]}
          />
        </div>
      </section>
    </main>
  );
}
