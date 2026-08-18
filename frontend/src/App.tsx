import { Navigate, Route, Routes } from "react-router-dom";

import { AppSidebar } from "./components/AppSidebar";
import { CreateProjectPage } from "./pages/CreateProjectPage";
import { ProjectWorkspacePage } from "./pages/ProjectWorkspacePage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import "./styles/app.css";

export default function App() {
  return (
    <div className="app-shell">
      <AppSidebar />
      <Routes>
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/new" element={<CreateProjectPage />} />
        <Route path="/projects/:projectId" element={<ProjectWorkspacePage />} />
        <Route path="/system" element={<SystemStatusPage />} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
}
