# spec-conformance-2026-09 — otaman-core canon-vs-reality self-report

Roman-directed stability audit (spec-agent 20260831T185126). Scope: materialized
capability specs in `otaman-specs/openspec/specs/` whose domain is otaman-core,
plus significant delivered behavior with no materialized spec home. Findings only
— no spec edits (spec-agent authors repairs), no code fixes.

Confidence is marked per finding: **[verified]** = checked against code/tests
this session; **[known]** = asserted from direct implementation knowledge.

Test suite at audit time: **957 tests** collected (`uv run pytest`).

---

## DRIFT / SPEC-WRONG (needs a spec repair)

### D1 — `terminal-auth-rbac`: auth is no longer runner-only; it moved to a shared core module  [verified]
- **Spec says:** "The runner SHALL expose `POST /api/auth/login`" (line 13); "attach tokens SHALL be issued by **the runner**" (line 42). Attribution is runner-only throughout.
- **Code does:** the login / attach-token / JWT issue+verify service was extracted (ce-refresh-token 1.1) into a **host-agnostic** `otaman_core.web_auth` (`CeAuthManager`). The **bridge** mounts it as the runner-free CE surface; the **runner** re-mounts the same module (EE). "There SHALL NOT be two auth implementations." Runner's `terminal/auth.py` is now a 35-line re-export of the core module.
- **Which is right:** the **code**. terminal-auth-rbac predates the CE-hosts-web-auth decision (Roman 2026-08-27) and is now partially obsolete: it attributes to the runner what is a shared core service. The `ce-web-auth` delta (ce-refresh-token change) captures the new reality but isn't materialized. Suggest terminal-auth-rbac be reconciled to point at `otaman_core.web_auth` and the bridge-hosts-CE model.
- Evidence: `src/otaman_core/web_auth.py` (provenance header records the EE→AGPL relicense + host-agnostic intent); `tests/test_web_auth.py` (32 tests).

### D2 — `human-roster`: `email` is REQUIRED in canon but OPTIONAL in code  [verified]
- **Spec says:** "Each entry SHALL have `name` (string), **`email` (string)**, and `roles`" (line 13) — email required.
- **Code does:** `HumanRosterEntry.email: str | None = None`; the loader accepts a missing email (returns None) and only rejects a *present-but-empty* email. This was a deliberate change (hitl-default-approver D3): provisioning may enrol a day-one approver from a non-email SSH-key comment, and `otaman doctor` WARNs rather than a dead approval path.
- **Which is right:** the **code** — but the spec is internally inconsistent: the same spec now carries "the approver role SHALL be the single authoritative grant" (line 94, materialized from hitl-default-approver), and that feature's rationale *requires* email-optional. Line 13 wasn't updated to match. Suggest line 13 change `email` to optional with the doctor-WARN note.
- Evidence: `human_roster.py` `_coerce_entry`; `tests/test_human_roster.py::TestParseErrors::test_missing_email_is_optional`.

### D3 — `shared-contracts`: message-type registry is missing types core now enforces  [verified]
- **Spec says:** a fixed "Message Type Registry" (line 131+) + a broadcast whitelist (line 86). No `lifecycle-change` entry.
- **Code does:** `validate_message.VALID_TYPES` includes `lifecycle-change` (program-lifecycle-states D4), and `_BROADCAST_TYPES` whitelists it for `to: all`. The bus validator enforces both. So core accepts/validates a type the shared-contracts canon doesn't list.
- **Which is right:** the **code** (the type was Roman-approved via program-lifecycle-states). shared-contracts' registry + broadcast whitelist should add `lifecycle-change`. Worth a sweep: reconcile the full canon registry against `VALID_TYPES` (several types were added by changes over time).
- Evidence: `validate_message.py` VALID_TYPES/`_BROADCAST_TYPES`; `tests/test_validate_message.py::...::test_lifecycle_change_all_ok`.

---

## CONFORMS (implementation matches canon)

### agent-credential-access — the core-owned requirements CONFORM  [verified]
(The `otaman connection` CRUD CLI + propose-and-confirm are cli-agent's; below is core's slice.)
- **"cascade tenant→org→program, nearest wins"** → `connections.resolve` per-name merge; `tests/test_connections.py::TestCascade::test_program_overrides_org_for_one_key_others_still_resolve` (the spec scenario verbatim). CONFORMS.
- **"multi-target ssh-agent socket registry is the default backend"** → `ssh_registry.SshAgentRegistry` (per-target `{key,socket,pid}`, liveness, re-attach). CONFORMS.
- **"check tests reachability+auth, reports read-only by default; `--fix` self-heals"** → `connection_check.ConnectionChecker`; `test_read_only_default_never_heals` + `test_fix_self_heals` + end-to-end registry heal. CONFORMS.
- **"secret values SHALL NEVER be exposed"** (Q5) → every model (`Connection`/`AgentEntry`/`CheckReport`) is locations/refs only; asserted by `TestValuesNeverExposed` in test_connections / test_ssh_registry / test_connection_check, and `test_secrets`. CONFORMS.
- **"read the secret backend, add no storage"** → `missing_secret_refs(conns, list_keys())`; `_secrets.list_keys` is a values-free reader over the dotenv backend. CONFORMS.

### human-roster (approver requirement) CONFORMS  [verified]
- `APPROVER_ROLE = "approver"`, `resolve_roster_human`/`is_approver`/`resolve_approver`, doctor `check_approver_config` (ERROR no-approver-when-live incl. empty/absent roster; WARN approver-missing-email). `tests/test_human_roster.py`. CONFORMS (modulo D2 email note).

### hitl-confirmation CONFORMS (core slice)  [verified]
- Confirmation ledger `confirmations.py` (`append_confirmation`/`verify_confirmation`, 0600, content hash) + tenant `hitl.yaml` schema (`hitl-schema.yaml`) with the program **no-weakening** rule (`validate_platform` builtin). `tests/test_confirmations.py`, `tests/test_hitl_config_schema.py`. CONFORMS.

### bus-uri-addressing / workspace-resolution / agent-identity-resolution / pm-sync-adapter  [known]
- `bus/uri.py` (`parse`/`BusUri`, three address forms → canonical), `_resolve.py` (`.otaman` marker walk-up), `identity.py` (`resolve_enforcement_identity`), `pm_sync.py` (adapter Protocol + `PmAdapterCapabilities`). Believed CONFORMS; not re-verified requirement-by-requirement this pass (flagging honestly). One caveat: `_resolve` still carries **legacy `.maestro` / `MAESTRO_ROOT` fallbacks + the `maestro_root` identifier** — intentional 1.0-gated debt (audit-enforced), already on the spec backlog (msg 20260824T204211), not drift.

---

## UNSPECCED — delivered, no materialized spec home (the "missed specs" to find)

These have approved *change* deltas in `otaman-specs/openspec/changes/` but are **not yet materialized into `specs/`**, so the canon doesn't describe live, consumed behavior:

- **U1 — program lifecycle registry** (`lifecycle.py`): `read_program_state` is the single read point consumed by runner/bridge/fswatch/router/cli; append-history writer; doctor. **Live and load-bearing** — it featured in the 2026-08-30 otaman-dev incident investigation. High priority to materialize. [verified]
- **U2 — CE web-auth extraction + refresh** (`web_auth.py`): the host-agnostic `CeAuthManager` + password-free `issue_session_token` (refresh flow). This is the *reality* that D1's terminal-auth-rbac should defer to. [verified]
- **U3 — single-acting-session guard** (`acting_lock.py`): flock identity-lock, kill-9 auto-release, preempt marker. A `single-acting-session` spec dir exists — verify it's materialized to match the shipped 0.1 primitive. [verified]
- **U4 — persisted CheckReport store** (`connection_check.report_store_path`/`persist_reports`/`load_reports`): tenant-home, program-keyed; the 1.3→2.1 seam plugin renders. Not in the agent-credential-access materialized spec (which describes check but not the persisted store). [verified]
- **U5 — secret-chain write + tenant scope** (`_secrets`): tenant-scoped dotenv (`scope: tenant`), the 0600 `upsert_dotenv_secret` writer, and `list_keys`. agent-credential-access says "add no storage backend" (true — same dotenv store), but the *write path* + tenant scope are unspecced. [verified]
- **U6 — config validators** (`validate_platform.validate_connections`, connections/hitl JSON schemas) and the **git_host Azure DevOps adapter** (`git_host_azure.py`) — verify each has a spec home. [verified]

---

## Summary

- **3 DRIFT/SPEC-WRONG** (all resolve by fixing the SPEC, not the code): terminal-auth-rbac (auth moved to core), human-roster email (required→optional), shared-contracts registry (missing `lifecycle-change`).
- **CONFORMS** across the credential-access core slice, the approver roster, hitl-confirmation, and (believed) the resolution/URI/pm-sync specs.
- **6 UNSPECCED** clusters — mostly this cycle's changes whose deltas exist in `changes/` but aren't materialized into `specs/` canon; U1 (lifecycle) and U2 (web-auth) are the most load-bearing.

No code changes proposed here per the audit rules — findings for spec-agent's triage.
