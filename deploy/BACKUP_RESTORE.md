# Harbor Market backup and restore

Run these commands from the repository root on the deployment machine. They
operate on the Compose project selected by `.env` (or by `ENV_FILE`). They do
not publish MinIO and do not contact any remote host.

## Maintenance lock and watchdog behavior

Deploy, paired-backup, and restore operations serialize on the atomic host
lock at `.data/operation.lock`. Nested restore commands join their parent's
lock. The watchdog takes the same lock before checking health or reconciling
Compose, so it skips all restart activity while maintenance owns the lock.
Normal operations remove the lock only after verification or after the exact
prior writer state has been restored.

A partial or uncertain restore deliberately leaves both writers stopped and
preserves the lock. The lock is never treated as stale automatically, including
after a host restart. Inspect its owner and preservation reason before doing
anything else:

```bash
for field in operation pid started_at host preserve; do
  if [[ -f ".data/operation.lock/$field" ]]; then
    printf '%s: ' "$field"
    cat ".data/operation.lock/$field"
  fi
done
docker compose --env-file .env ps
```

Do not remove a preserved lock merely because its PID is no longer running.
To run a reviewed restore or deploy helper as part of recovery, explicitly
rejoin the preserved owner instead of deleting the lock first:

```bash
export HARBOR_MARKET_OPERATION_LOCK_DIR="$PWD/.data/operation.lock"
export HARBOR_MARKET_OPERATION_LOCK_TOKEN="$(cat \
  "$HARBOR_MARKET_OPERATION_LOCK_DIR/owner")"
```

Joined helpers cannot remove the parent lock. Keep this token in the recovery
shell only; unset both variables when recovery is complete.

First prove that the database and object bucket are a matching pair, activate
the matching reviewed code, start the intended writers, and run
`deploy/verify.sh` while the lock still blocks the watchdog. Only after that
verification succeeds may an operator clear the maintenance state. Use the
guarded writer-start and verification command in the failure-recovery section
below; it removes the lock only on success.

## Create a paired release backup

Use the paired command before every deployment or rollback:

```bash
deploy/backup-release.sh
```

The script records whether `backend` and `cleanup-worker` are running, stops
both writers, dumps PostgreSQL, mirrors the private MinIO bucket, hashes every
artifact, writes the release `.complete` marker last, and restores the prior
service state. The returned directory defaults to:

```text
~/HarborMarketBackups/releases/release-YYYYMMDDTHHMMSSZ
```

Do not copy or retain only part of this directory. A complete release contains
`database.sql.gz` with `.sha256` and `.metadata` sidecars, `object-store/` with
`manifest.tsv` and `.complete`, and the release-level `.complete` marker.

Optional destinations:

```bash
RELEASE_BACKUP_DIR=/Volumes/Encrypted/harbor-releases deploy/backup-release.sh
RELEASE_BACKUP_DESTINATION=/Volumes/Encrypted/pre-v2 deploy/backup-release.sh
ENV_FILE=/absolute/path/to/production.env deploy/backup-release.sh
```

The object-backup directory must be on a path shared with the Docker engine
(the default path under the current user's home directory is supported by
Docker Desktop and Colima). The backup command performs a host/container
read-write mount handshake and fails before publishing a backup if that path is
not actually shared; this prevents a silently empty MinIO backup.

Writer shutdown allows 60 seconds for in-flight work by default. Set
`BACKUP_STOP_TIMEOUT_SECONDS` to a value from 10 through 300 when a larger
upload/import shutdown window is required.

The component commands remain available for diagnostics, but two independently
timed component backups are not a transactionally consistent release pair:

```bash
deploy/backup-db.sh
deploy/backup-objects.sh
```

## Restore a paired release

Restoring replaces both current data stores. Verify the path, then run:

```bash
deploy/restore-release.sh --confirm-replace \
  ~/HarborMarketBackups/releases/release-YYYYMMDDTHHMMSSZ
```

Before mutation, the restore validates the release format, configured database
and bucket names, compressed database stream, database SHA-256, object manifest
SHA-256, every object SHA-256, object count, and total bytes. It then:

1. Stops `cleanup-worker` and `backend` while leaving PostgreSQL and MinIO up.
2. Creates pre-restore database and object rollback backups.
3. Exactly restores MinIO and verifies that the source and bucket do not differ.
4. Drops and freshly recreates the application database before loading the SQL
   dump; it never overlays rows in the existing database.
5. Writes a paired rollback release and restores the previous service state
   only after both phases succeed.

The successful command prints a path similar to:

```text
Paired pre-restore rollback release: ~/HarborMarketBackups/releases/pre-restore-release-TIMESTAMP-PID
```

Keep that directory until application and media integrity have been verified:

```bash
deploy/verify.sh
```

## Roll back a successful restore or deployment

Use the paired pre-change or pre-restore release, never a database and bucket
from different timestamps. A deployment rollback is **code-revision first**:
do not restore old data and then allow the newly deployed code to restart
against it.

1. Record the failed revision, the exact previously reviewed 40-character Git
   SHA, and the matching paired release path. Keep a reviewed copy of the
   compatible recovery scripts available if the target revision predates the
   current backup format.
2. Acquire the shared operation lock and stop `backend` and `cleanup-worker`.
   Keep that parent shell and its exported lock token alive for the whole
   rollback; the watchdog will skip reconciliation.
3. With writers still stopped, check out the previous SHA and build its backend
   and frontend images without running `compose up` yet. Reject a dirty
   worktree rather than overwriting local files.
4. Run the compatible paired restore. Because both writers were already
   stopped, the restore leaves them stopped after success.
5. Start Compose from the previous revision, run `deploy/verify.sh`, then
   release the operation lock. If any step fails, preserve the lock and keep
   writers stopped for manual recovery.

Before checking out older code, copy the currently reviewed recovery tools to a
path outside the worktree. The essential command order is:

```bash
(
  set -euo pipefail
  : "${FAILED_REVISION:?set FAILED_REVISION to the failed 40-character SHA}"
  : "${PREVIOUS_REVIEWED_SHA:?set PREVIOUS_REVIEWED_SHA to the rollback SHA}"
  : "${ROLLBACK_RELEASE:?set ROLLBACK_RELEASE to the paired backup path}"
  test -z "$(git status --porcelain --untracked-files=all)"

  RECOVERY_DEPLOY_DIR="$HOME/HarborMarketRecovery/deploy-$FAILED_REVISION"
  test ! -e "$RECOVERY_DEPLOY_DIR"
  mkdir -p "$(dirname "$RECOVERY_DEPLOY_DIR")"
  cp -Rp deploy "$RECOVERY_DEPLOY_DIR"
  export PROJECT_DIR="$PWD"
  source "$RECOVERY_DEPLOY_DIR/mac-common.sh"
  docker_bin="$(find_docker)"
  env_file="$PROJECT_DIR/.env"
  rollback_complete=false

  rollback_finish() {
    local status="$?"
    trap - EXIT INT TERM
    if [[ "$rollback_complete" == true ]]; then
      operation_lock_release || status=1
    else
      compose "$docker_bin" --env-file "$env_file" stop --timeout 60 \
        cleanup-worker backend || true
      operation_lock_preserve "manual rollback requires recovery" || true
      status=1
    fi
    exit "$status"
  }

  operation_lock_acquire manual-rollback
  trap rollback_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  compose "$docker_bin" --env-file "$env_file" stop --timeout 60 \
    cleanup-worker backend
  git checkout --detach "$PREVIOUS_REVIEWED_SHA"
  compose "$docker_bin" --env-file "$env_file" config --quiet
  compose "$docker_bin" --env-file "$env_file" build backend frontend
  PROJECT_DIR="$PROJECT_DIR" ENV_FILE="$env_file" \
    "$RECOVERY_DEPLOY_DIR/restore-release.sh" --confirm-replace \
    "$ROLLBACK_RELEASE"
  compose "$docker_bin" --env-file "$env_file" \
    up --detach --no-build --remove-orphans
  ENV_FILE="$env_file" /bin/bash deploy/verify.sh
  rollback_complete=true
)
```

Do not substitute `deploy/mac-deploy.sh` for the build-only step: it starts and
verifies writers before the matching rollback data has been restored. The
operation-lock functions in `deploy/mac-common.sh` provide
`operation_lock_acquire manual-rollback`, `operation_lock_preserve <reason>`,
and `operation_lock_release` for an operator-controlled parent shell. On any
failure before verification, call
`operation_lock_preserve "manual rollback requires recovery"` (or simply leave
the owning shell without releasing the lock) and keep both writers stopped.

Each restore creates another paired rollback release, so the operation remains
reversible until those backups are intentionally removed.

If either restore phase fails, the component script first attempts to restore
its automatic rollback. The paired script also returns the object bucket to its
pre-restore state when the later database phase fails. On every partial or
uncertain failure or process interruption, `backend` and `cleanup-worker`
remain stopped. Destructive restore phases are fail-closed and only re-enable
automatic writer restart after a complete target restore or a completely
verified automatic rollback. Inspect the
reported error and rollback paths, repair or repeat the paired restore, then
start writers only after both stores and the active code revision are confirmed
coherent. Keep the preserved lock in place during this check so the watchdog
cannot intervene:

```bash
if (
  set -euo pipefail
  recovery_verified=false

  recovery_finish() {
    local status="$?"
    trap - EXIT INT TERM
    if [[ "$recovery_verified" == true ]]; then
      if ! rm -rf -- .data/operation.lock; then
        printf 'Verification passed, but the lock could not be cleared.\n' >&2
        status=1
      fi
    else
      if ! docker compose --env-file .env stop --timeout 60 \
        cleanup-worker backend; then
        printf 'CRITICAL: recovery writers could not be stopped.\n' >&2
      fi
      status=1
    fi
    exit "$status"
  }

  trap recovery_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  docker compose --env-file .env start backend cleanup-worker
  /bin/bash deploy/verify.sh
  recovery_verified=true
); then
  unset HARBOR_MARKET_OPERATION_LOCK_DIR HARBOR_MARKET_OPERATION_LOCK_TOKEN
else
  printf 'Verification did not complete; the lock is preserved. Confirm writers are stopped.\n' >&2
  false
fi
```

Never use `docker compose down -v` during backup or recovery. It deletes the
PostgreSQL volume. Never remove `MINIO_DATA_DIR`; it contains the live object
store.

## Restore one component only

This is for deliberate recovery when the operator has proved that the other
store is already from the matching point in time:

```bash
deploy/restore-db.sh --confirm-replace /path/to/database.sql.gz
deploy/restore-objects.sh --confirm-replace /path/to/object-store
```

Both commands validate completion metadata, stop `backend` and
`cleanup-worker`, create an automatic component rollback, and restore the prior
service state on success. Database backups created before the v2 checksum and
metadata format, and object backups created before the v2 manifest format, are
rejected rather than restored without integrity verification.
