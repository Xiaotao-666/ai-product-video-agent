import { NavLink } from "react-router-dom";

function navigationClass({ isActive }: { isActive: boolean }): string {
  return `nav-item${isActive ? " nav-item-active" : ""}`;
}

export function AppSidebar() {
  return (
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
        <NavLink className={navigationClass} to="/projects">
          <span className="nav-icon" aria-hidden="true">
            01
          </span>
          Projects
        </NavLink>
        <NavLink className={navigationClass} to="/system">
          <span className="nav-icon" aria-hidden="true">
            02
          </span>
          System Status
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        <span className="local-indicator" aria-hidden="true" />
        Local only
      </div>
    </aside>
  );
}
