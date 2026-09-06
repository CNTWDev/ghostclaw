# ADR-0014: Local database evolution and at-rest protection

Status: accepted

## Context

HushClaw is a local-first, single-user application. Its SQLite database holds
conversation turns, memories, tasks, file metadata, connector inbox data, and
event projections. SQLite remains a good fit for this workload: transactions,
WAL concurrency, FTS5, JSON queries, and zero service operations are all useful.

The previous schema path had two material risks:

1. a monolithic list of idempotent SQL ran on every startup and ignored every
   `OperationalError`, so a genuine migration failure could look like success;
2. the database, WAL/SHM files, migration snapshots, and export archives relied
   on the caller's umask and could be readable by other local accounts.

Filesystem permissions do not isolate data from another process running as the
same login user. Field-by-field encryption is also a poor fit: it would leak
metadata, complicate every query, and disable useful FTS/JSON behavior.

## Decision

- Keep one SQLite transactional boundary for the personal distribution. Split
  databases only when independently deployable domains or retention policies
  require it; table count alone is not a reason to add distributed consistency.
- Version 7 establishes an immutable `schema_migrations` ledger. Each future
  migration has a version, name, checksum, and atomic transaction. Unknown
  future schemas are rejected rather than silently downgraded.
- The pre-v7 migration list runs once while crossing the ledger boundary. Only
  a verified duplicate-column collision is ignored; other errors fail startup.
- Migration upgrades create a recovery snapshot, then run SQLite `quick_check`
  and `foreign_key_check` before accepting the new version.
- The data directory is owner-only (`0700` on POSIX). The database, WAL/SHM,
  migration snapshots, secret file, and exported backup archive are owner-only
  (`0600`). Restored data trees are hardened before use.
- SQLite uses an application id, a bounded busy timeout, a WAL size limit,
  in-memory temp storage, and `secure_delete=FAST`.
- A `turns` delete trigger removes the matching FTS row. The v7 migration also
  removes historical orphan FTS rows left by retention, so expired transcript
  text cannot remain searchable after its canonical row is deleted.

## Strong at-rest encryption

One-click installs use SQLCipher for the whole database. A random 256-bit key
is stored in the platform credential vault (macOS Keychain, Windows
current-user DPAPI, or Linux Secret Service) and is applied before any schema
read. Headless systems without a usable vault fall back to an owner-only key
file and surface that weaker key source in `database status` and `doctor`.
This preserves FTS5 and existing query behavior while encrypting the database
and its SQLite sidecars.

The lifecycle is part of the same release:

1. verified plaintext-to-SQLCipher export with atomic cutover and rollback;
2. explicit recovery-key export for offline custody;
3. encrypted, portable backups that exclude the device-local key material;
4. SQLCipher-aware read pools, doctor checks, imports, and migration backups;
5. a clear locked-state error when the vault or driver is unavailable.

Migration snapshots are converted before the live database. Each file is
exported into a temporary SQLCipher database, integrity checked, atomically
renamed, reopened and checked again. The operation is idempotent and resumable.
The installer enables `memory.database_encryption = "sqlcipher"` only after the
live cutover succeeds.

The pure-Python core retains zero mandatory third-party dependencies for
embedding. The supported one-click installer deliberately includes the
`encryption` runtime extra; explicitly choosing `database_encryption = "off"`
is an advanced development/embedding choice rather than the product default.

## Consequences

Plain SQLite tools and unrelated local applications can no longer read the
database or its packaged backups. Historical data, FTS indexes and schema
migrations remain intact. SQLCipher does not protect unlocked data from
HushClaw itself, an attacker controlling the logged-in account, process-memory
inspection, screen capture, or an already-compromised operating system.
Full-disk encryption (FileVault, BitLocker, or LUKS) remains recommended.

Key rotation is intentionally separate follow-up work: rotation must cover the
live database, every retained snapshot, and exported archives with a tested
recovery protocol. Merely issuing `PRAGMA rekey` against the live file would
create a misleading and incomplete guarantee.
