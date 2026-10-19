# `careerhub.core`

The bottom layer. Every other package depends on it; it depends on nothing else in Career Hub.

| Module | What it does | Ticket | Status |
| --- | --- | --- | --- |
| `storage` | Opens the encrypted database; the only code that does | CH-14 | Next |
| `secrets` | Creates and reads keys in the macOS Keychain | CH-14 | Next |
| `models` | The 14 entities from the PRD as typed models | CH-14 | Next |
| `settings` | Loads user settings from the data folder | CH-15 | Planned |
| `logging` | Logs with personal details redacted | CH-15 | Planned |
| `runlog` | Records each pipeline run | CH-15 | Planned |
| `backup` | Nightly encrypted backup with `VACUUM INTO` | CH-15 | Planned |

Design decisions: [ADR-0001, encrypted storage](../../../docs/decisions/0001-encrypted-storage.md).
