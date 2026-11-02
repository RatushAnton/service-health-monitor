# SQLite Backup and Recovery

## Status

Phase 7G.5C implements consistent SQLite snapshots, integrity
validation, temporary restoration tests, and backup retention.

Raspberry Pi deployment and automated backup scheduling were
validated on November 2, 2026. Full disaster-recovery cutover
testing remains outstanding.

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
- The Pi's daily systemd backup timer is enabled and active.
- The Mac LaunchAgent performs scheduled off-device verification.

## Verification

Development verification completed on November 2, 2026:

- Backup-specific tests: 15 passed
- Full Python test suite: 109 passed
- Ruff lint and formatting: passed
- Backup CLI: create, verify and restore-check available

Raspberry Pi deployment, manual SQLite backup, temporary restore verification, and off-device export to the Mac were validated on November 2, 2026. A full disaster-recovery cutover remains untested.

## Automated Backup Scheduling — Phase 7G.5E

### Raspberry Pi

The daily backup service and timer are stored in:

- deploy/pi/systemd/service-health-monitor-backup.service
- deploy/pi/systemd/service-health-monitor-backup.timer

The timer runs at 03:15 in the Raspberry Pi's local timezone.

It invokes the existing SQLite online backup implementation
inside the running worker container and keeps three local copies.

A failed backup returns a nonzero exit status and is visible
through the systemd journal.

The timer does not require changes to Docker's configuration,
the Raspberry Pi boot parameters or unrelated containers.

### macOS

The Mac retrieves the latest available Pi backup with:

    scripts/pull-latest-pi-backup.sh

The script requires:

- An SSH host alias named healthmon-backup-pi
- Non-interactive SSH key authentication
- An isolated Python virtual environment in the installed runtime
- The existing pull-pi-backup.sh transfer script

The LaunchAgent installer is:

    scripts/install-mac-backup-agent.py

The installer creates a user-level LaunchAgent scheduled every
six hours with RunAtLoad enabled.

The Mac scheduler is independent of the Pi's daily timer.

If the Mac is offline, the Pi continues creating backups.
The Mac can fetch the latest retained backup when it reconnects.

### Limitations

- Three local backup copies are retained on the Pi.
- Local backups share the Pi's SD-card failure domain.
- Off-device copies are not automatically pruned.
- If the Mac is offline longer than local retention covers,
  some historical backup snapshots may never be exported.
- SSH key authentication is required for unattended exports.
- Scheduling does not replace a full disaster-recovery test.
- The monitoring host's existing power instability remains
  an operational risk.

Both schedulers were activated and verified on November 2, 2026.
The installer prepares files but does not activate or reload launchd.


## Self-contained macOS installer

The installer stages a versioned runtime under:

    ~/Library/Application Support/service-health-monitor/

The runtime includes the backup scripts, SQLite backup module, and
an isolated Python virtual environment. A `current` symlink points
to the selected release.

For a new installation:

    python3 scripts/install-mac-backup-agent.py

For an existing installation:

    python3 scripts/install-mac-backup-agent.py --update

The update preserves the existing LaunchAgent's SSH alias, backup
destination, scheduling interval and logging configuration.

The installer does not load or reload the LaunchAgent.
Activation must be performed separately after validation.

Existing backups and previous releases are not deleted automatically.
The application runtime is independent of the development repository.

The installer should not be run against a working production
LaunchAgent until the replacement has passed validation.
