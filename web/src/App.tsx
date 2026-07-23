import { Routes, Route, Navigate } from "react-router"
import { Layout } from "@/components/layout"
import WorkspacePage from "@/pages/workspace"
import OutlinePage from "@/pages/outline"
import MaterialsPage from "@/pages/materials"
import SystemPage from "@/pages/system"

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/workspace" replace />} />
        <Route path="/workspace" element={<WorkspacePage />} />
        <Route path="/workspace/:novelId" element={<WorkspacePage />} />
        <Route path="/outline" element={<OutlinePage />} />
        <Route path="/materials" element={<MaterialsPage />} />
        <Route path="/system" element={<SystemPage />} />
      </Routes>
    </Layout>
  )
}
