#!/bin/bash
# Run the daily trades and start/sit digest. Driven by launchd twice a day.
#
# launchd starts jobs with almost no environment: no PATH beyond the system
# default, no shell profile, no working directory. Everything the job needs is
# therefore set explicitly here rather than inherited.
#
# Secrets come from backend/.env, which is gitignored. They are exported into
# the process, never echoed, and never written to the log.

set -uo pipefail

REPO="$HOME/Projects/ffb26"
BACKEND="$REPO/backend"
LOG_DIR="$HOME/Library/Logs/ffb"
LOG="$LOG_DIR/digest.log"

# uv lives in a user-local bin that launchd does not put on PATH.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"
}

# Keep the log from growing without bound: a run is small, but this fires
# twice a day for a whole season.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ]; then
    mv "$LOG" "$LOG.1"
fi

cd "$BACKEND" || { log "FATAL: cannot cd to $BACKEND"; exit 1; }

# Load .env without printing it. `set -a` exports everything defined inside.
#
# Secrets live outside the repo. This job ran for six days without posting
# because the repo then sat under ~/Desktop, which macOS privacy protection
# (TCC) puts off limits to an unattended job: sourcing backend/.env failed
# with "Operation not permitted", so DISCORD_WEBHOOK_URL was never set. The
# repo has since moved, but keeping the secrets at a stable path outside it
# means a future relocation cannot break the schedule the same way.
ENV_FILE="$HOME/.local/ffb/.env"
[ -f "$ENV_FILE" ] || ENV_FILE="$BACKEND/.env"

if [ -r "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$ENV_FILE"
    set +a
else
    log "WARN: no readable env file, running without a token or webhook"
fi

if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
    log "WARN: DISCORD_WEBHOOK_URL is not set, the digest will not be posted"
fi

# Belt and braces. The digest cannot write to Sleeper regardless, but an
# unattended job is the last place that should be left to chance.
unset FFB_ALLOW_WRITES

log "starting digest"
OUTPUT=$(uv run python -m ffb.alerts.digest --limit 3 2>&1)
STATUS=$?

echo "$OUTPUT" >> "$LOG"

# Exit 1 means something could not be evaluated. That is not the same as
# "nothing to report", and it is the failure a scheduled job hides best, so it
# gets its own line in the log.
if [ $STATUS -ne 0 ]; then
    log "FINISHED WITH PROBLEMS (exit $STATUS): something could not be evaluated"
else
    log "finished clean"
fi

exit $STATUS
