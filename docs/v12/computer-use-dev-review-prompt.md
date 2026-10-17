# Handoff prompt — reviewing Radal v12 on the REAL dev cloud with computer use

Target: **https://dev.radalseguros.cl** (AWS: CloudFront → Lambda, RDS MySQL, S3, DeepInfra AI). This is the
deployed build, not a local demo. Unlike the local review, **AI is ON** (real extraction, comparison and agent),
**scheduled jobs run** (cuota clock, UF/USD rates), and files go to S3. Mail is still OFF (no Zoho credentials yet),
Forpay is PAUSED by decision, and the HDI portal is not connected.

## For the human, before starting

1. Make sure the deploy of `dev` is green (GitHub Actions "Deploy Backend (dev)" and "Deploy Frontend (dev)").
2. Have at hand two small real-looking PDFs: an insurance **policy** (póliza) and a **cotización**. Any
   Chilean policy PDF works; an anonymised one is best.
3. Start a computer-use session (Claude with the computer tool). Give it a browser window (Chrome) and paste
   everything between the `=====` lines. Put the PDFs on the Desktop so it can upload them.

=====

You are reviewing **Radal v12** on its real cloud environment, **https://dev.radalseguros.cl**, using the
computer (screen, mouse, keyboard). Radal is a web app for Chilean insurance brokers (*corredoras de seguros*).
The UI is in **Spanish (Chile)**. Review it as a demanding product designer and QA lead would. You do not
have access to the code and you do not fix anything. Write your report in **Spanish**.

### Ground rules

- This is a **shared dev environment** that the team also uses. Work ONLY inside the demo broker *Corredora
  Demo SpA*. Never edit or delete data of other brokers, never change passwords, and never invite real email
  addresses (use `@example.com`).
- Every write you make (payments, policies, links) stays in dev. That is fine, but name anything you create
  with the prefix **"QA "** so the team can find it.
- Take a screenshot for every finding. Note the exact URL and the time (Chile).
- If something takes longer than 60 s, note it, take a screenshot and move on. The cloud cuts requests at
  60 s; a long AI comparison may hit that limit, and that is a finding.

### Logins (password `radal1234`)

| user | role |
|---|---|
| `admin@demo.cl` | broker administrator (Corredora Demo SpA) |
| `ejecutivo@demo.cl` | broker executive, the **owner** of the demo accounts (receives their to-dos) |
| `admin@radal.cl` | Radal platform staff → Mantenedor |

Public share links (open them in a private/incognito window, logged out):
```
<filled in by the orchestrator after seeding dev — see docs/v12/dev-demo-links.md>
```

### What v12 is (the features to judge)

- **Pólizas** is the home. Policies arrive by **PDF upload (the AI reads it)**, by manual entry, or through the
  full journey. Each policy shows what is missing (*Falta*), its parties, its cobranza and the journey stages
  that actually happened.
- **Partial journeys:** groups (*Asegurados*) and accounts (*cuentas / colocaciones*) show only the stages
  that happened. Skipped stages appear as "omitida".
- **Closed ramo catalogue:** ramo → subtipo comes from a fixed list.
- **Cobranza:** each cuota is at one of three levels. *Registro* means record only. *Asistida* means
  reminders and asking the insurer for the status. *En línea* (Forpay) is paused, so no cuota should offer
  online payment. There are payments, promises, PAC rejections, reversals, reminders (mail OFF, so they are
  recorded as "omitido"), inquiries to insurers, and the "Respuestas por confirmar" inbox. Every broker
  confirmation is required: Radal never marks a cuota paid on its own.
- **Public links** for the insured (cuotas, payment instructions, receipt upload) and for the insurer
  (report a cuota's status).
- **Notifications:** a bell with *Por hacer* / *Para saber*, and for admins a **Mías / Todas** switch.
- **Configuración:** Correo, Cobranza (cadence, tolerances, per-message template approval), Compañías y cobro.
- **Mantenedor** (`admin@radal.cl`): insurer collection profiles, provider accounts with write-only secrets,
  channels, webhook events, and job runs (you should see scheduled runs of `collection.clock` and `fx.daily`).
- **Agente:** an AI assistant with skills and tools. It is ON in dev. Every write it proposes needs your
  confirmation.
- **Cotizar en portales:** HDI is not connected, so expect a clear "no se ejecutará / en configuración"
  state, never "En cola" forever.

### Walk (in order)

1. **Pólizas (admin@demo.cl):** the KPI cards (each card replaces the previous filter), the filters, and
   each demo policy's blocks.
   - Upload the policy PDF with **Subir póliza**. Watch the progress bar, then review the AI reading on
     *Confirmar datos*: are the fields right, and are the low-confidence ones flagged? Confirm it.
   - Check that it landed in the right group/account, that its *Viaje* says it entered with the policy, and
     that the payment plan was read. Load the plan if it wasn't.
   - Note how long the AI took.
2. **Registrar a mano** a "QA" policy with Chilean number formats (`1.234.567`, `12,5`) and a wrong total
   premium first. Then load a payment plan (watch for past-date warnings and the "iniciar cobranza" option).
3. **Cobranza.**
   - Tabs and filters.
   - On one cuota, register a partial payment in CLP (`150.000`), then confirm an insurer reply that says
     "pagada". The total must never exceed the cuota, and the panel, the plan page and the KPI must agree.
   - Promise, PAC rejected, reverse with a reason.
   - Reminder preview: amount = the **balance**, greeting = the actual recipient.
   - Inquiry to the insurer: a second one for the same cuota must be refused with a link to the first.
4. **Public links (logged out):** the insured view (instructions, upload a receipt → "Comprobante en
   revisión" persists on reload; no "Pagar" because Forpay is paused) and the insurer view (report a status).
   Then log in as **ejecutivo@demo.cl** (the owner) and find the new to-dos.
5. **Notifications:** as admin, switch **Mías / Todas**. As the executive, check the overdue / at-risk cuotas
   appear as *Por hacer*.
6. **Asegurados → an account with skipped stages:** the rail, *Saltar a…*, *Activar etapa* (and its reason),
   *Otras etapas* (it must not offer going back once Vigente; the menu must be fully visible), and the
   **Bitácora** (Spanish labels, Chile time, one collapsed "N etapas omitidas" line).
7. **The full journey with AI**, on a new "QA" account:
   - Upload an antecedente.
   - Upload two cotizaciones → *Comparar* (timing; does it finish within 60 s, and if not, what does the
     user see?).
   - Generate the propuesta.
   - Judge the comparison's quality: does the table read correctly, and does the recommendation make sense?
8. **Agente:** activate the *Revisión de cobranza* skill and ask "¿Qué cuotas están en riesgo y qué le
   pregunto a cada compañía?". Check the answer against the Cobranza screen. Ask it to register a payment:
   it must PROPOSE, and only write after you confirm.
9. **Configuración** (all tabs, as admin): approve one template only and check the others are unaffected.
   Log in as the executive and check every disabled control says why.
10. **Mantenedor (admin@radal.cl):** profiles, provider accounts (secrets never shown back), *Verificar*
    result reflected on the card, webhook URL shown with the API origin, jobs list with recent scheduled runs.
11. **Mobile:** use the browser's responsive mode at 375 px and repeat Pólizas, a policy, Cobranza + side
    panel, the bell, a public link (both audiences), Configuración and an account page.

### Look for

- **Errors and correctness:** errors, blank screens, endless spinners, actions that do nothing, wrong or
  inconsistent numbers, times shifted by 3 h.
- **Language:** raw keys like `collections.state.overdue` or `case_file.skipped_to`, English text or enum
  values, wrong vocabulary. The team's words are Póliza, Cuota, Colocación/Cuenta, Asegurado, Compañía,
  Cobranza, Vigencia.
- **No dead buttons:** every control either works or is disabled with a reason you can read, also on touch.
- **Privacy:** public pages must never show other policies, commissions or internal notes. The Mantenedor must
  never echo a secret.
- **Cloud-specific:** slow first loads (cold starts), uploads that fail, downloads that 404 (S3), AI that
  hangs, and job runs missing from the Mantenedor.

### Report (Spanish)

Start with a 5-line summary: overall readiness, top 3 problems, and how the AI performed (accuracy + timing).
Then one entry per finding, most severe first:

```
### [Error | Mejora | Funcionalidad] <título>
- Severidad: crítica | alta | media | baja
- Dónde: URL + pantalla (+ ancho 1440/375)
- Usuario: …
- Pasos: 1… 2… 3…
- Qué pasó / Qué debería pasar: …
- Hora (Chile) y captura: …
```

End with a list of what you could not test and why. Do not invent findings. If you are unsure, say so.

=====
