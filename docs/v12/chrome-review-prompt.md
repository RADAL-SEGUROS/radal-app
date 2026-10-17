# Handoff prompt — reviewing Radal v12 with Claude in Chrome

## For the human, before starting the session (2 minutes)

1. From the repo root: `./review-v12.sh`. It seeds a **fresh** demo world in `/tmp/radal-review.db` (the cuotas are
   computed relative to today, so run it the day of the review), starts the backend on `:8000` and the frontend
   on `:5500` with **AI, mail and RPA switched off** (nothing leaves the machine), and prints the logins plus
   **three public share links**. Copy those three `/s/…` lines into the prompt below where it says `SHARE LINKS`.
2. Open Chrome with the Claude extension, open `http://localhost:5500`, start a new conversation and paste
   everything between the two `=====` lines.
3. When finished: `Ctrl-C` in the terminal (or `./review-v12.sh stop`).

=====

You are reviewing **Radal v12**, a web app for Chilean insurance brokers (*corredoras de seguros*). It runs
locally at **http://localhost:5500**. Use Chrome to review it the way a demanding product designer and QA lead
would: click through every screen, try the real flows, try to break them, and report what is wrong or confusing.
You are reviewing. You do not fix code and you have no repository access. The app UI is in **Spanish (Chile)**.
Write your report in Spanish.

### Context you need

- **Users.** The broker (*corredora*) is the only real user of the app. Insurers (*compañías*) and the insured
  (*asegurados*) do NOT log in. They receive **public links** (WhatsApp-ready) instead. Radal staff have a
  separate console, the **Mantenedor**.
- **What v12 changed** (focus your review here):
  1. **Pólizas is the home and the main view.** A policy can come from an uploaded PDF, from manual entry
     (*Registrar a mano*), or from the full journey.
  2. **Partial journeys.** The *grupo → cuenta (colocación)* views work as a *viewer*. They show only the
     stages that actually happened, and skipped stages appear as "omitida". From an account you can
     *Saltar a…* a later stage, or *Activar* a skipped stage, but only while there is no policy yet.
  3. **Closed ramo catalogue.** Ramo → subtipo comes from a fixed list. Brokers can no longer create ramos.
  4. **Cobranza in three levels per cuota.** *En línea* means native online payment, via Forpay, which is not
     connected in this demo. *Asistida* means Radal reminds the payer and asks the insurer for the payment
     status by email. *Registro* means record only. Insurer replies become *suggestions* that the broker
     confirms in "Respuestas por confirmar". Radal never marks a cuota paid on its own from an email.
  5. **Public share links** `/s/:token`. The insured view shows the cuotas, the insurer's payment
     instructions, and an upload for the payment receipt. The insurer view lets the insurer report a cuota's
     status.
  6. **Notifications.** A bell with two trays: *Por hacer* (to do) and *Para saber* (for information).
  7. **Configuración.** Correo (custom SMTP/IMAP; Google/Microsoft show "pronto"), Cobranza (cadence,
     tolerances, message templates and their approval), Compañías y cobro.
  8. **Mantenedor** (`/platform`, Radal staff only): insurer collection profiles, Forpay provider accounts
     with write-only secrets, channels, webhook events, and scheduled job runs.
  9. **Cotizar en portales.** An RPA search showcase for HDI. The real portal is not connected, so it shows
     explained error states.
  10. **Agente.** An AI assistant with *skills*. The AI provider is OFF in this demo, so a message should
      fail with a clean Spanish error.
- **Expected in this demo (verify that they fail gracefully; do not report them as bugs):**
  - "Pagar" on the insured link fails with a clean Spanish error (Forpay is not connected yet, by decision).
  - Emails are recorded but not sent. A reminder or inquiry may show "omitido".
  - The agent can't answer (no AI key).
  - Portal search can't reach HDI.
  - Google/Microsoft mail are disabled with a "pronto" reason.
  - WhatsApp sending is "pronto". The *share via WhatsApp* button only opens wa.me.

### Logins (password `radal1234` for all)

| user | role | lands on |
|---|---|---|
| `admin@demo.cl` | broker administrator | /policies |
| `ejecutivo@demo.cl` | broker executive (no Configuración rights) | /policies |
| `admin@radal.cl` | Radal platform staff | /platform |

**SHARE LINKS** (open them while LOGGED OUT, e.g. in an Incognito window, prefixed with `http://localhost:5500`):
```
<paste the three "[insurer] …" / "[insured] …" lines printed by ./review-v12.sh here>
```

### What to do, in this order

1. **Broker admin, desktop (≈1440 px wide).**
   - Pólizas: KPI cards (click each; does the list filter?), all filters including ramo → subtipo, search by
     policy number and by RUT with dots.
   - Open each of the 5 policies and read the *Falta*, *Partes*, *Cobranza* and *Viaje* blocks. Use
     *Completar* on a missing field. Edit the parties. Use *Compartir* to create a link for the insured and one
     for the insurer, then revoke one.
   - Registrar a mano: create a new policy (try a wrong total premium, amounts typed the Chilean way such as
     `1.234.567` and `12,5`, and a missing subtipo). Check where the new policy lands and what its *Viaje* says.
   - Subir póliza: open the dialog and check the copy. The AI is off, so don't expect extraction to work.
2. **Cobranza.**
   - Tabs Próximas / Vencidas / Al día / Saldadas, the filters, and the level badges (hover or tap them for
     the reason).
   - Open a cuota's side panel. Register a partial payment in CLP (`150.000`), then the rest. Promise a
     payment date, mark a PAC rejected, reverse a payment (with reason), send a reminder (preview first), and
     ask the insurer (*Consultar a la compañía*). Try *Link de pago* on an HDI cuota and on a non-HDI one.
   - "Respuestas por confirmar": confirm, or choose another cuota. Also check "Consultas a compañías" and the
     plan detail page.
3. **Notifications:** the bell, both trays, opening an item, and the full `/notifications` page.
4. **Asegurados (groups) and accounts.**
   - Open a group and an account whose stages were skipped: the journey rail, "omitida" ticks, *Saltar a…*,
     *Activar etapa* (and its disabled reason), and "Ver etapas omitidas".
   - Create a new account: the ramo → subtipo cascade, and confirm there is no way to create a ramo.
5. **Configuración** (all tabs): save cobranza settings with Chilean number formats, edit and approve a
   template, the custom mailbox form validation (don't enter real credentials), and an insurer override.
6. **Log in as `ejecutivo@demo.cl`:** what is hidden or disabled, and whether every disabled control says
   *why*.
7. **Public links, logged out:** both audiences. Upload a small image or PDF as a receipt, report a cuota
   status as the insurer, and try "Pagar". Then log back in as the admin and find the new evidence and the
   new notification. Also try a broken token (change one character of a link).
8. **`admin@radal.cl` → Mantenedor:** edit an insurer profile, create a provider account (secrets must never
   be shown back), rotate the webhook secret, and look at events and jobs. Confirm a broker user cannot see
   or open `/platform`.
9. **Cotizar en portales and Agente:** the portal cards, credentials panel, failed-run explanation, starting a
   search, toggling skills, and sending one message.
10. **Mobile.** Resize the window to ~375 px wide (or use DevTools device mode, iPhone 12/13) and repeat the
    key screens: Pólizas list and detail, Registrar a mano, Cobranza and its side panel, the bell, a public
    link (both audiences), Configuración and an account page. Look for horizontal scrolling, cut-off
    buttons, unreachable actions, and disabled reasons that only appear on hover (that is a bug on touch).

### What to look for everywhere

- **Broken:** errors, blank screens, spinners that never end, actions that do nothing, wrong numbers (totals
  that don't add up, a cuota still "vencida" after full payment, counts on cards ≠ rows in the list).
- **Language:** raw keys such as `collections.state.overdue` or `policies:new.title`, English text, raw
  English enum values (`active`, `payment`), typos, inconsistent vocabulary. The team's words are Póliza,
  Cuota, Colocación/Cuenta, Asegurado, Compañía, Cobranza, Endoso, Siniestro, Vigencia.
- **No dead buttons:** every control either works or is visibly disabled AND says why (a "pronto" chip or a
  reason you can read, also on touch).
- **Money and dates:** UF with comma decimals (`UF 12,50`), CLP without decimals (`$150.000`), dates in Chilean
  format, no times shifted by 3 hours.
- **Privacy:** the public pages must never show other policies, commissions, internal notes or anything of
  another broker. The Mantenedor must never echo a secret.
- **UX:** confusing flows, unclear labels, too many clicks, missing empty states or confirmations,
  inconsistent layouts between similar screens, and anything that would make a broker on their phone give
  up.

Take a screenshot of anything you report when you can.

### Report format (Spanish)

Start with a 5-line summary: overall readiness and the top 3 problems. Then one entry per finding, most severe
first, using the team's issue categories:

```
### [Error | Mejora | Funcionalidad] <título corto>
- Severidad: crítica | alta | media | baja
- Dónde: URL completa + pantalla/sección (y ancho: 1440 o 375 px)
- Usuario: admin@demo.cl | ejecutivo@demo.cl | admin@radal.cl | público (asegurado/compañía)
- Pasos: 1… 2… 3…
- Qué pasó: …
- Qué debería pasar: …
- Captura: (si la tomaste)
```

(*Error* = something is broken; *Mejora* = it works but should be different; *Funcionalidad* = something is
missing.) End with a short list of what you could not test and why. Do not invent findings. If you are unsure
whether something is a bug, say so and explain what you saw.

=====
