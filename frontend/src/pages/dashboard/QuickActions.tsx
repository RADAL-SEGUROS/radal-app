import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { UserPlus, FilePlus2, ShieldAlert, ClipboardCheck } from "lucide-react";
import { Button } from "@/components/ui/button";

interface QuickAction {
  key: string;
  labelKey: string;
  icon: React.ReactNode;
  to: string;
}

const ACTIONS: QuickAction[] = [
  {
    key: "nuevoCliente",
    labelKey: "quickActions.nuevoCliente",
    icon: <UserPlus className="h-4 w-4" />,
    to: "/clientes?nuevo=1",
  },
  {
    key: "nuevaPoliza",
    labelKey: "quickActions.nuevaPoliza",
    icon: <FilePlus2 className="h-4 w-4" />,
    to: "/polizas?nuevo=1",
  },
  {
    key: "reportarSiniestro",
    labelKey: "quickActions.reportarSiniestro",
    icon: <ShieldAlert className="h-4 w-4" />,
    to: "/siniestros?nuevo=1",
  },
  {
    key: "solicitarInspeccion",
    labelKey: "quickActions.solicitarInspeccion",
    icon: <ClipboardCheck className="h-4 w-4" />,
    to: "/inspecciones?nuevo=1",
  },
];

export function QuickActions() {
  const { t } = useTranslation("dashboard");
  const navigate = useNavigate();

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {ACTIONS.map((action, i) => (
        <Button
          key={action.key}
          type="button"
          variant={i === 0 ? "primary" : "secondary"}
          onClick={() => navigate(action.to)}
          className="justify-start"
        >
          {action.icon}
          <span className="truncate">{t(action.labelKey)}</span>
        </Button>
      ))}
    </div>
  );
}
