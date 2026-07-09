import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";

import Login from "@/pages/auth/Login";
import DashboardPage from "@/pages/dashboard";
import ClientesPage from "@/pages/clientes";
import ClienteDetail from "@/pages/clientes/Detail";
import PolizasPage from "@/pages/polizas";
import PolizaDetail from "@/pages/polizas/Detail";
import RenovacionesPage from "@/pages/renovaciones";
import RenovacionDetail from "@/pages/renovaciones/Detail";
import CotizacionesPage from "@/pages/cotizaciones";
import SiniestrosPage from "@/pages/siniestros";
import SiniestroDetail from "@/pages/siniestros/Detail";
import InspeccionesPage from "@/pages/inspecciones";
import InspeccionDetail from "@/pages/inspecciones/Detail";

export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />

      {/* Protected app shell */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="clientes" element={<ClientesPage />} />
          <Route path="clientes/:id" element={<ClienteDetail />} />
          <Route path="polizas" element={<PolizasPage />} />
          <Route path="polizas/:id" element={<PolizaDetail />} />
          <Route path="renovaciones" element={<RenovacionesPage />} />
          <Route path="renovaciones/:id" element={<RenovacionDetail />} />
          <Route path="cotizaciones" element={<CotizacionesPage />} />
          <Route path="siniestros" element={<SiniestrosPage />} />
          <Route path="siniestros/:id" element={<SiniestroDetail />} />
          <Route path="inspecciones" element={<InspeccionesPage />} />
          <Route path="inspecciones/:id" element={<InspeccionDetail />} />
        </Route>
      </Route>

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
