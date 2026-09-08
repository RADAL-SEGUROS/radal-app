import * as React from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/providers/AuthProvider";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { LanguageToggle } from "@/components/layout/LanguageToggle";

const schema = z.object({
  email: z.string().email(),
  password: z.string().min(1),
});

type FormValues = z.infer<typeof schema>;

export default function Login() {
  const { t } = useTranslation(["auth", "common"]);
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [error, setError] = React.useState<string | null>(null);

  // The app starts at groups (spec §5.4); "/" only ever redirects there, so a
  // login with no `from` must land on /groups directly and never on /leads.
  const from =
    (location.state as { from?: { pathname: string } } | null)?.from
      ?.pathname ?? "/groups";

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  const onSubmit = async (values: FormValues) => {
    setError(null);
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch {
      setError(t("auth:errors.invalidCredentials", "Credenciales inválidas"));
    }
  };

  return (
    <div className="flex min-h-screen w-full bg-bg-app">
      {/* Brand panel (dark Ink) */}
      <div className="relative hidden w-1/2 flex-col justify-between overflow-hidden bg-[#0e0f10] p-12 lg:flex">
        {/* single soft brand glow — one accent per surface */}
        <div
          aria-hidden
          className="pointer-events-none absolute -right-24 -top-24 h-[28rem] w-[28rem] rounded-full opacity-25 blur-3xl"
          style={{
            background:
              "radial-gradient(circle, var(--brand) 0%, transparent 70%)",
          }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-white/10"
        />
        <div className="relative flex items-center gap-3">
          <img
            src="/brand/radal-mark-white.svg"
            alt="Radal"
            className="h-9 w-9"
          />
          <span className="wordmark text-h2 text-white">
            Radal.
          </span>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-display tracking-tight text-white">
            {t("auth:brand.title", "Conecta la industria del seguro")}
          </h2>
          <p className="mt-4 text-body text-white/70">
            {t(
              "auth:brand.subtitle",
              "Plataforma operativa para corredoras de seguros.",
            )}
          </p>
        </div>
        <p className="relative text-caption text-white/40">
          {t("common:app.tagline")}
        </p>
      </div>

      {/* Form panel */}
      <div className="relative flex w-full flex-col items-center justify-center bg-bg-app px-6 lg:w-1/2">
        <div className="absolute right-4 top-4 flex items-center gap-1">
          <ThemeToggle />
          <LanguageToggle />
        </div>

        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2 lg:hidden">
            <img
              src="/brand/radal-mark-teal.svg"
              alt="Radal"
              className="h-8 w-8"
            />
            <span className="wordmark text-h2 text-text-primary">
              Radal.
            </span>
          </div>

          <h1 className="text-h1 tracking-tight text-text-primary">
            {t("auth:login.title", "Iniciar sesión")}
          </h1>
          <p className="mt-1 text-body text-text-muted">
            {t("auth:login.subtitle", "Ingresa a tu cuenta")}
          </p>

          <form
            onSubmit={handleSubmit(onSubmit)}
            className="mt-8 space-y-4"
            noValidate
          >
            <div className="space-y-1.5">
              <Label htmlFor="email">
                {t("auth:fields.email", "Correo electrónico")}
              </Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                placeholder="tu@correo.cl"
                aria-invalid={!!errors.email}
                {...register("email")}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">
                {t("auth:fields.password", "Contraseña")}
              </Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••"
                aria-invalid={!!errors.password}
                {...register("password")}
              />
            </div>

            {error ? (
              <p className="rounded-sm bg-neg-soft px-3 py-2 text-caption text-neg-text">
                {error}
              </p>
            ) : null}

            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : null}
              {t("auth:login.submit", "Ingresar")}
            </Button>
          </form>

          {import.meta.env.DEV ? (
            <div className="mt-6 rounded-md border border-line bg-paper-2 p-3">
              <p className="text-caption font-medium text-ink-3">
                {t("auth:demo.title", "Credenciales demo")}
              </p>
              <p className="mt-1 font-mono text-[12px] text-text-secondary">
                jose@radalseguros.cl · radal1234
              </p>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
