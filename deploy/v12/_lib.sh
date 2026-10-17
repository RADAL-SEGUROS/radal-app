# shellcheck shell=bash
# Shared helpers for the deploy/v12 scripts. Sourced, never executed.
#
# Contract (every script):
#   * DRY_RUN=1 is the DEFAULT. In dry-run NOTHING calls AWS — not even
#     describe/get. Every read-only check prints "would check" and reads as
#     "absent", so the full create path is printed; every mutation prints
#     "would run" and yields a <PLACEHOLDER> id for the next step.
#   * DRY_RUN=0 applies. It first verifies that the profile points at the
#     expected account, then runs describe-before-create for every resource,
#     so re-running is a no-op.
#   * Plain bash 3.2 (macOS /bin/bash): no associative arrays, no mapfile.

set -euo pipefail

DRY_RUN="${DRY_RUN:-1}"
PROFILE="${PROFILE:-radal}"                 # never [default]
REGION="${REGION:-us-east-1}"
ACCOUNT_ID="${ACCOUNT_ID:-185011028331}"
REDACT="${REDACT:-}"                        # a secret value to mask in every printed command

is_dry() { [ "$DRY_RUN" != "0" ]; }
log()  { printf '\n==> %s\n' "$*" >&2; }
note() { printf '    %s\n' "$*" >&2; }
warn() { printf '    WARNING: %s\n' "$*" >&2; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Quote one word for display: bare when it is shell-safe, else single-quoted.
_q() {
  case "$1" in
    "" ) printf "''";;
    *[!A-Za-z0-9_./:=,@+%-]*) printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")";;
    * ) printf '%s' "$1";;
  esac
}

# Quote a command line for display (copy-pasteable), masking $REDACT.
_fmt() {
  local out="" a
  for a in "$@"; do out="$out $(_q "$a")"; done
  out="${out# }"
  if [ -n "$REDACT" ]; then out="${out//"$REDACT"/<redacted>}"; fi
  printf '%s' "$out"
}

# Read-only AWS query -> text on stdout ("" when absent / not found).
# Dry-run: prints the check and returns "" without calling anything.
aws_read() {
  if is_dry; then
    printf '    [dry-run] would check: aws %s\n' "$(_fmt "$@")" >&2
    return 0
  fi
  local out
  out=$(aws "$@" --profile "$PROFILE" --region "$REGION" --output text </dev/null 2>/dev/null) || out=""
  if [ "$out" = "None" ]; then out=""; fi
  printf '%s' "$out"
}

# Mutating AWS call: aws_do <placeholder> <aws args...> -> text on stdout.
# Dry-run: prints the command and echoes <placeholder> so later steps can be shown.
# Call it as a plain assignment (X=$(aws_do ...)), never with `local X=$(...)`,
# so a failure stops the script under `set -e`.
aws_do() {
  local placeholder="$1"; shift
  if is_dry; then
    printf '    [dry-run] would run:   aws %s\n' "$(_fmt "$@")" >&2
    printf '%s' "$placeholder"
    return 0
  fi
  printf '    + aws %s\n' "$(_fmt "$@")" >&2
  local out
  out=$(aws "$@" --profile "$PROFILE" --region "$REGION" --output text </dev/null) || {
    printf '    aws call failed: aws %s\n' "$(_fmt "$@")" >&2
    return 1
  }
  if [ "$out" = "None" ]; then out=""; fi
  printf '%s' "$out"
}

# Mutating AWS call whose output is not needed.
aws_run() { aws_do "" "$@" >/dev/null; }

# Value or placeholder: or_placeholder "$VALUE" "<NAME>"
or_placeholder() { if [ -n "$1" ]; then printf '%s' "$1"; else printf '%s' "$2"; fi; }

preflight() {
  if is_dry; then
    log "DRY_RUN=1 (default): printing commands only — no AWS call is made, not even describe/get."
    note "Checks read as 'absent' in dry-run, so the whole create path is shown. Re-run with DRY_RUN=0 to apply."
    return 0
  fi
  command -v aws >/dev/null 2>&1 || die "aws CLI not found"
  local acct
  acct=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text) \
    || die "cannot call sts with profile '$PROFILE'"
  [ "$acct" = "$ACCOUNT_ID" ] || die "profile '$PROFILE' is account $acct, expected $ACCOUNT_ID — refusing"
  log "APPLYING to account $ACCOUNT_ID ($REGION) with profile '$PROFILE'."
}
