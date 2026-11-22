import { useEffect } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import Layout from "@/components/Layout";
import { Spinner } from "@/components/ui";
import { useMe } from "@/hooks/queries";
import LoginPage from "@/pages/Login";
import HomePage from "@/pages/Home";
import HeatmapPage from "@/pages/Heatmap";
import GridPage from "@/pages/Grid";
import AvailabilityPage from "@/pages/Availability";
import ProjectsPage from "@/pages/Projects";
import ProjectDetailPage from "@/pages/ProjectDetail";
import PersonPage from "@/pages/Person";
import PeoplePage from "@/pages/People";
import RequestsPage from "@/pages/Requests";
import ImportsPage from "@/pages/Imports";
import AdminPage from "@/pages/Admin";

export default function App() {
  const me = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();

  useEffect(() => {
    const h = () => {
      qc.setQueryData(["me"], undefined);
      qc.invalidateQueries({ queryKey: ["me"] });
      nav("/login");
    };
    window.addEventListener("rah:unauthorized", h);
    return () => window.removeEventListener("rah:unauthorized", h);
  }, [qc, nav]);

  if (me.isLoading) return <Spinner label="Starting" />;
  if (!me.data) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }
  return (
    <Layout me={me.data}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/heatmap" element={<HeatmapPage />} />
        <Route path="/grid" element={<GridPage />} />
        <Route path="/availability" element={<AvailabilityPage />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:id" element={<ProjectDetailPage />} />
        <Route path="/people" element={<PeoplePage />} />
        <Route path="/people/:id" element={<PersonPage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/imports" element={<ImportsPage />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
