import { SystemStatusPage } from "./pages/SystemStatusPage";
import "./styles/app.css";

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            AV
          </span>
          <div>
            <strong>AI Product</strong>
            <span>Video Agent</span>
          </div>
        </div>

        <nav aria-label="Primary navigation">
          <p className="nav-label">WORKSPACE</p>
          <a className="nav-item nav-item-active" href="/" aria-current="page">
            <span className="nav-icon" aria-hidden="true">
              01
            </span>
            System Status
          </a>
          <span className="nav-item nav-item-disabled" aria-disabled="true">
            <span className="nav-icon" aria-hidden="true">
              02
            </span>
            Projects
            <small>Soon</small>
          </span>
        </nav>

        <div className="sidebar-footer">
          <span className="local-indicator" aria-hidden="true" />
          Local only
        </div>
      </aside>

      <SystemStatusPage />
    </div>
  );
}
