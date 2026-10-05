import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Toaster } from "./components/Toast";
import LoginPage from "./pages/Login";
import PlanPage from "./pages/Plan";
import LivePage from "./pages/Live";
import TripsPage from "./pages/Trips";
import FleetPage from "./pages/Fleet";
import DataPage from "./pages/Data";
import SettingsPage from "./pages/Settings";
import JobsPage from "./pages/Jobs";

export function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/trips" replace />} />
          <Route path="/plan" element={<PlanPage />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/trips" element={<TripsPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="*" element={<Navigate to="/trips" replace />} />
        </Route>
      </Routes>
      <Toaster />
    </>
  );
}
