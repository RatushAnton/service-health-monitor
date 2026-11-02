# SQLite Backup and Recovery

## Status

Phase 7G.5C implements consistent SQLite snapshots, integrity
validation, temporary restoration tests, and backup retention.

The automated tests run on the development machine. Raspberry Pi
deployment, scheduling, and off-device recovery verification are
not yet complete.

## Backup design

The implementation is provided by `healthmon.backup`.

Commands:

- `create`: create a consistent, verified SQLite backup
- `verify`: validate an existing SQLite database
- `restore-check`: restore into temporary storage and validate records

Backups are generated using SQLite's online backup API. This avoids
copying a live database while it is being modified.

Backups are verified before publication and are not overwritten.
Retention removes only matching older backup files after successful
backup creation.

## Raspberry Pi storage layout

The proposed deployment uses:

- Active database: `/var/lib/service-health-monitor/health.db`
- Local backups: `/var/lib/service-health-monitor/backups/`
- Docker service: `worker`
- Compose configuration: `compose.pi.yaml`
- Compose project: `service-health-monitor-pi`

Both database and local backups currently use the same Docker volume
on the Pi's SD card.

Important: This is not protection against SD-card failure. Verified
off-device backups are required for disaster recovery.

## Create a backup on the Pi

Only after Phase 7G.5 deployment, from
`~/service-health-monitor-prod`:

    docker compose -f compose.pi.yaml exec -T worker \
      python -m healthmon.backup create \
      --db /var/lib/service-health-monitor/health.db \
      --out-dir /var/lib/service-health-monitor/backups \
      --keep 3

The output provides the exact generated backup filename.

The local retention count is initially three backups to limit
SD-card consumption.

## Verify an existing backup

Use the exact filename returned by the create operation:

    docker compose -f compose.pi.yaml exec -T worker \
      python -m healthmon.backup verify \
      --db /var/lib/service-health-monitor/backups/BACKUP_FILENAME

For a restoration drill:

    docker compose -f compose.pi.yaml exec -T worker \
      python -m healthmon.backup restore-check \
      --db /var/lib/service-health-monitor/backups/BACKUP_FILENAME

Replace BACKUP_FILENAME with a real generated filename.

Restore-check creates a temporary independent database, validates
its integrity and reads critical application records.

It does not overwrite or restart the running service.

## Copy a backup to the Mac

The repository includes `scripts/pull-pi-backup.sh`.

Run it from the Mac after the Raspberry Pi deployment exists:

    ./scripts/pull-pi-backup.sh \
      ratush@PI_HOST \
      BACKUP_FILENAME \
      "$HOME/Documents/healthmon-backups"

Replace PI_HOST and BACKUP_FILENAME.

The script:

1. Retrieves the remote backup's SHA-256 checksum.
2. Streams that backup through SSH into a temporary Mac file.
3. Confirms the transferred file's checksum.
4. Performs a local SQLite restore-check.
5. Publishes the verified file without overwriting existing backups.

The Mac backup directory is outside the Git repository and
uses private permissions.

The Pi's admin token is not copied.

## Failure handling

If backup creation fails:

- Do not remove existing backups.
- Inspect available disk space and worker logs.
- Do not delete the live SQLite database.
- Do not run Docker volume cleanup commands.

If transfer or checksum verification fails:

- Treat the backup transfer as unsuccessful.
- Preserve previously verified backups.
- Investigate SSH connectivity, Docker service status and disk space.

If restore-check fails:

- Do not use the backup for recovery.
- Preserve other verified backups.
- Investigate database integrity and restore errors.

## Recovery policy

Never overwrite the active SQLite database while the worker or
admin service is running.

A future disaster-recovery exercise will restore a verified backup
into a separate database volume, validate application behavior,
and document a controlled service cutover.

The temporary restore-check is a prerequisite for that exercise,
not a substitute for full production recovery testing.

## Security and limitations

- Backup files can contain monitor configuration and session metadata.
- Protect backup files as sensitive operational data.
- Keep backups and credentials out of Git.
- Do not expose the admin HTTP interface publicly.
- The Pi's existing undervoltage history remains a reliability risk.
- Local retention is count-based, not a maximum disk-size limit.
- Automated backup scheduling is not yet configured.
- Off-device backups remain manual until scheduling is implemented.

## Verification

Development verification completed on November 2, 2026:

- Backup-specific tests: 14 passed
- Full Python test suite: 108 passed
- Ruff lint and formatting: passed
- Backup CLI: create, verify and restore-check available

No Raspberry Pi deployment or restore operation was performed.
