# Quick redeploy and disaster recovery

Status: **DR READY to consume the declared private inputs**. The target is a working stack on a new empty host from
this repository and Infisical, without restoring optional runtime backups.
Required external inputs and normal first-use procedures are listed below.
Do not run fresh-host tools on an initialized deployment.

## Minimal required backup

Configuration templates belong in Git. Private declarative values belong in
Infisical. Runtime databases, identity and certificates remain outside both.
The only mandatory runtime artifact is **`data/npm/data/database.sqlite`**,
restored from the future encrypted DR layer before NPM starts. The observed file
is 405,504 bytes (396 KiB) and passed SQLite integrity checking. A future backup
must be a consistent single-file snapshot, not an arbitrary hot copy.

| Classification | Logical datasets | Treatment |
| --- | ---: | --- |
| REQUIRED_IDENTITY_STATE | 0 | Re-enroll/regenerate optional identities |
| REQUIRED_USER_STATE | 1 | NPM database only |
| OPTIONAL_RUNTIME_STATE | 21 | Start fresh or restore for convenience |
| DISPOSABLE_STATE | 11 | Initialize empty and warm up |

NPM's DB contains routing, forwarding, access lists/policy, certificate
associations and users. Those declarations cannot currently be rebuilt from
Infisical alone. Its loss prevents recovery of the intended proxy configuration.
Certificate/key files are optional: [the NPM recovery procedure](NPM-RECOVERY.md)
reissues the wildcard with Let's Encrypt and Cloudflare DNS-01, reusing
`/shared/CLOUDFLARE_API_TOKEN`. Infisical Certificate Management is not used.

AIOStreams and AIOMetadata DBs are now OPTIONAL_RUNTIME_STATE. Their recovery inputs are **PRIVATE_DR_PAYLOAD** logical exports, kept outside
this repository in the future SOPS/age DR layer, plus private field/UUID bindings
from Infisical. The versioned fresh-only importer is documented in
[AIO export/import](AIO-EXPORT-IMPORT.md). Neither entire database is required.

## State that may start empty

- **tailscale-node** — `OPTIONAL_RUNTIME_STATE`. A new node can enroll against its configured control server. The current setup uses a custom control server. Loss: New node identity/address; routes and approvals must be accepted again.
- **headscale-enrollments** — `OPTIONAL_RUNTIME_STATE`. Users and nodes can be registered again using the Headscale administrator CLI; loss of old enrollments is accepted for quick redeploy. Loss: Existing API/preauth keys and node registrations are not recreated by merely supplying their old values.
- **headscale-noise** — `OPTIONAL_RUNTIME_STATE`. The control server can generate a new Noise key for a newly enrolled network. Loss: Previous control-server identity is lost; clients may need re-enrollment.
- **headscale-sockets** — `DISPOSABLE_STATE`. Sockets and process runtime files are recreated. Loss: No persistent user data lost.
- **headplane-local** — `OPTIONAL_RUNTIME_STATE`. A fresh Headplane can bootstrap its first owner after a valid Headscale API key is available. Loss: Preferences and role assignments must be reapplied.
- **npm-tls** — `OPTIONAL_RUNTIME_STATE`. The wildcard is reissued by NPM/Certbot with Let's Encrypt and Cloudflare DNS-01. Domain/contact metadata come from the restored DB; the API token comes from /shared/CLOUDFLARE_API_TOKEN. Loss: Issuance requires reachable ACME/Cloudflare services and available issuance quota; expired sessions or ACME accounts may be recreated.
- **npm-generated** — `DISPOSABLE_STATE`. Nginx files/logs are generated from routing configuration. Loss: Logs and generated files are lost.
- **portainer-local** — `OPTIONAL_RUNTIME_STATE`. The stack is declared in Compose; a fresh local endpoint and administrator can be initialized. Loss: Portainer UI preferences and local admin account are reset.
- **teamspeak-local** — `OPTIONAL_RUNTIME_STATE`. The service can create a fresh server and bootstrap administrator privileges. Loss: New server identity; previous channels/ACLs and client permissions are lost.
- **microwarp-registration** — `OPTIONAL_RUNTIME_STATE`. MicroWARP supports fresh provider registration. Loss: New VPN device identity, subject to provider availability.
- **easyproxy-registration** — `OPTIONAL_RUNTIME_STATE`. The declared proxy config is rendered separately and runtime tunnel identity can be recreated. Loss: A new tunnel/provider registration may be needed.
- **seanime-preferences** — `OPTIONAL_RUNTIME_STATE`. Extension code and private defaults are declarative; library metadata/history can be regenerated. Loss: History and local preferences reset; rescan/re-authenticate linked accounts as necessary.
- **seanime-media** — `OPTIONAL_RUNTIME_STATE`. The inspected media/download trees currently contain no files; there is no demonstrated unique media set to require restoring. Loss: Any subsequently added irreplaceable files must be reclassified REQUIRED_USER_STATE.
- **seanime-shared-preferences** — `OPTIONAL_RUNTIME_STATE`. Extension code and private defaults are declarative; library metadata/history can be regenerated. Loss: History and local preferences reset; rescan/re-authenticate linked accounts as necessary.
- **seanime-shared-media** — `OPTIONAL_RUNTIME_STATE`. The inspected media/download trees currently contain no files; there is no demonstrated unique media set to require restoring. Loss: Any subsequently added irreplaceable files must be reclassified REQUIRED_USER_STATE.
- **jackett-runtime** — `DISPOSABLE_STATE`. Indexer definitions/API keys are declarative; instance ID, session/hidden data and caches can regenerate. Loss: New instance/session state; indexers may require normal re-authentication.
- **aiostreams-user-config** — `OPTIONAL_RUNTIME_STATE`. The selected DR model uses logical configuration export/import and private field/UUID bindings from Infisical, not a mandatory SQLite backup. Loss: User configuration is rebuilt from the separate PRIVATE_DR_PAYLOAD and Infisical bindings; the private payload must be prepared before losing the original host.
- **aiostreams-caches** — `DISPOSABLE_STATE`. These are rebuildable datasets and transient sessions, not the user configuration subset. Loss: Cache warmup and new logins.
- **aiometadata-user-config** — `OPTIONAL_RUNTIME_STATE`. The selected DR model uses logical configuration export/import and private field/UUID bindings from Infisical, not a mandatory SQLite backup. Loss: User configuration is rebuilt from the separate PRIVATE_DR_PAYLOAD and Infisical bindings; the private payload must be prepared before losing the original host.
- **aiometadata-oauth** — `OPTIONAL_RUNTIME_STATE`. Provider sessions can be authorized again. Loss: User reauthorization is necessary.
- **aiometadata-caches** — `DISPOSABLE_STATE`. Mappings and metadata are downloaded/regenerated. Loss: Initial warmup and slower first requests.
- **postgres-cluster** — `OPTIONAL_RUNTIME_STATE`. The cluster and three service databases/roles can be initialized from declarative credentials. Backup is an accelerator, not a startup prerequisite. Loss: Current application records are lost according to their own classifications below.
- **postgres-companion** — `DISPOSABLE_STATE`. This companion mount is not the PostgreSQL 18 PGDATA location; the public postgresql.conf is a separate bind. Loss: No declared unique user-state dependency for this companion mount.
- **comet-cache-db** — `DISPOSABLE_STATE`. Torrent/index/cache/search state can be rebuilt from configured sources. Loss: Cache warmup and background rescraping.
- **comet-session-files** — `OPTIONAL_RUNTIME_STATE`. Session keys may regenerate; PUBLIC_API_TOKEN is already supplied through the canonical shared Infisical mapping. Loss: Sessions expire; configured external API token remains the declarative value.
- **cometnet-node** — `OPTIONAL_RUNTIME_STATE`. Quick redeploy accepts a new node identity; remote bootstrap peers are declarative. Loss: Peers see a new node; any manual pinning requires normal re-approval.
- **cometnet-cache** — `DISPOSABLE_STATE`. Node discovery/index state is reconstructible. Loss: Peer discovery and index warmup.
- **stremthru-cache** — `DISPOSABLE_STATE`. Cache/result state does not gate service startup. Loss: Cache warmup.
- **stremthru-user-runtime** — `OPTIONAL_RUNTIME_STATE`. Configured operator auth and vault key are declarative; accounts can be re-added/re-authorized during first use. Loss: Runtime-added account associations/settings are lost.
- **redis-data** — `DISPOSABLE_STATE`. The declared consumers use Redis for cache/session data; no unique durable queue or user records are declared. Loss: Sessions/logins and cache entries are lost.
- **aiomanager-state** — `OPTIONAL_RUNTIME_STATE`. The manager can initialize a fresh schema and first-use configuration. Loss: Saved manager associations and user-added configurations must be added again.
- **dnscrypt-cache** — `DISPOSABLE_STATE`. Resolver caches/lists/logs are obtained from public sources. Loss: Resolver list download/warmup.

No optional/disposable state row is a preflight backup gate. Empty writable
bind directories are created by `prepare-state`; Docker creates fresh named
volumes on first start. The DR override uses `external: false` and project-owned
volume names, including replacements for anonymous PostgreSQL/StremThru mounts.
It is for a **new host only** and is not activated on the current deployment.

Tailscale uses a custom control server in this deployment. On a fresh Headscale
database, old API/preauth tokens are not recreated by supplying their old strings.
Create a user and valid enrollment key on the new control server and re-enroll
nodes; create a new API key for Headplane. Do this only on the replacement host,
then reconcile its private configuration through Infisical. A valid old key has
not been proven sufficient for fresh enrollment. Restrict Headplane exposure
while its first owner is established. This is normal bootstrap, not an old
identity-restore requirement. See [Headscale registration](https://headscale.net/stable/ref/registration/),
[Headscale API](https://headscale.net/stable/ref/api/) and
[Headplane owner setup](https://headplane.net/features/sso).

Portainer and TeamSpeak may establish new administrators; MicroWARP and other
provider clients may register again. Seanime libraries/history can be rebuilt
and extensions are declarative. No unique media files were found in the inspected
Seanime media trees; subsequently added irreplaceable media must be reclassified.
Jackett definitions and indexer configuration are rendered; sessions and instance
state can regenerate. Redis starts empty for the declared cache/session consumers.
Comet/CometNet torrent databases, indices and caches are disposable; a new node
identity is accepted. Their old volumes are not startup prerequisites.

NPM certificates are reissued through its own Certbot implementation. The
replacement-only helper quarantines affected hosts during issuance, handles old
or new certificate IDs automatically and verifies associations and HTTPS.
Legacy numbered credential files are not mandatory pre-start inputs. Detailed
commands and validation limits are in `NPM-RECOVERY.md`.

## Required external input contracts

The public consumer is complete; actual input provisioning is a separate step.
The following inputs must exist on the replacement host, not in Git:

- Infisical snapshots for all paths in `config/dr-render-plan.json`.
- `/oauth2-proxy/ALLOWED_EMAILS`: explicit private email allowlist, one email per
  line. Rendering writes `data/oauth2-proxy/allowed-emails.txt` mode 0640.
  Missing, empty, duplicate or wildcard policies fail closed. This job does not
  import that key or infer addresses from another service.
- `/dr/AIO_PROFILE_BINDINGS`: stable private AIO identities/passwords and optional
  profile metadata; newly declared, not provisioned by this job.
- External AIOStreams/AIOMetadata PRIVATE_DR_PAYLOAD matching the public schemas.
- The consistent encrypted-layer NPM database artifact.

The protected live Agent mapping audit passed on 2026-09-30:
**AGENT_MAPPING_REAL=PASS**, **INITIAL_AGENT_MAPPING=CLOSED**. An operator ran
the updated verifier with sudo against the applied Agent configuration; its
real output confirmed both service mappings and ended with `TOTAL PASS`.
This evidence is recorded separately from synthetic template tests. Repeat
the read-only check on a fresh host or after changing the Agent mappings:

```sh
sudo python3 -B scripts/verify-agent-dr-mappings.py --target "$PWD"
```

It outputs only service/folder/destination/key names and status, reads no auth
credential files, and never edits/restarts Agent. The public renderer explicitly
maps `/aiometadata` and `/aiostreams` to their service env files, with the declared
AIOStreams shared-key aliases. `CONFIG_ACCESS_KEY` remains in the `/aiostreams`
export: its conditional quoting changes formatting only, not its source. Unknown
or ambiguous template expressions fail closed. The real check neither renders
secrets nor proves fresh-host startup; those remain separate checks. On a fresh
host use these explicit contracts and validate any mapping change reported by
the helper before substituting another Agent configuration.

Honey/Seanime template security reviews are complete. One optional Honey icon
uses a generic public asset and one legacy provider mirror uses the author's
published default, only in the fresh template. Live config is unchanged.

`QUICK_REDEPLOY_WITH_MINIMAL_REQUIRED_BACKUP` is tested with synthetic equivalents
of these inputs. It does not assert that a private DR repository, encrypted
payload archive or new Infisical key has already been created.

## Fresh host procedure

Prerequisites: Python 3.11+, POSIX ACL tools, Docker Engine/Compose, Infisical
CLI/Agent, an available/restored Infisical project and Universal Auth credentials
provided through a separate secure channel. Those credentials are never Git
content. Install the host-side packages in `scripts/requirements-dr.txt`; the
read-only synthetic test suite additionally uses Node.js with native crypto. The pinned image digests in `config/image-lock.json` must remain
available and compatible with the new host architecture. Do not change the
PostgreSQL major during a recovery that chooses to reuse database contents.

On a genuinely empty clone, after supplying the declared private inputs:

```sh
python3 -B scripts/dr.py init --target "$PWD"
python3 -B scripts/dr.py agent-config --target "$PWD" \
  --project-id "$INFISICAL_PROJECT_ID" --environment prod \
  --address "$INFISICAL_ADDRESS" \
  --client-id-file "$CLIENT_ID_FILE" --client-secret-file "$CLIENT_SECRET_FILE"
```

Use the installed CLI's `agent --help` to run the generated
`.generated/dr-agent.json` on the new host as root with the deployment group and
UMask 0027. It renders only `.secrets/dr-inputs/*.goenv` snapshots, has no
`execute.command`, and never writes live app-owned configuration. Wait for all
required snapshots; never use empty substitutes for missing real private values.

```sh
sudo python3 -B scripts/dr.py render --target "$PWD" --mode fresh
sudo python3 -B scripts/dr.py prepare-state --target "$PWD"
# Import external private logical payloads into NEW AIO databases.
sudo python3 -B scripts/aio-dr.py import --target "$PWD" --payload "$AIO_DR_PAYLOAD"
# Restore the consistent NPM database only; then prepare it before NPM starts.
sudo python3 -B scripts/npm-dr.py prepare --target "$PWD"
sudo python3 -B scripts/dr.py preflight --target "$PWD" --mode fresh
docker compose --profile all config --quiet
```

The default mode is `fresh`. Rendering validates all outputs and refuses existing
files/symlink parents. App-owned Honey, Headscale/Headplane, Jackett and Seanime
config is created once, never continuously overwritten. `prepare-state` creates
missing empty bind directories with explicit numeric ownership/mode and leaves
existing directories unchanged. It does not restore/copy data, create Docker
volumes or start containers. Use the actual deployment group with
`--deployment-gid` if its numeric ID differs.

Root `.env` is private and sets `COMPOSE_FILE` to the base Compose plus the DR
override; ordinary `docker compose` commands then use both without extra flags.
Private envs, generated config and actual init SQL remain ignored by Git.

## Empty PostgreSQL initialization

The public `data/postgres/postgresql.conf` and sanitized source
`config/dr-templates/postgres/init.sql.template` are repository inputs.
`scripts/dr.py` produces the ignored `data/postgres/init/init.sql` exclusively
for an empty cluster, from current Infisical database connection configuration
and `/pgbouncer/USERLIST_CONTENT`. Comet's authority-only connection-string
format is supported without changing the actual runtime environment value.

The generator verifies each connection password against the imported PgBouncer
SCRAM credential and preserves its verifier exactly in the fresh role setup.
Generating a different salt from the same password would break SCRAM forwarding;
see [PgBouncer authentication format](https://www.pgbouncer.org/config.html#authentication-file-format).
Unsupported authentication forms fail closed. The SQL creates the three service
roles/databases and preserves their public role settings; no existing DB is
queried or mutated by the generator. Application migrations create empty schemas.

The [PostgreSQL image entrypoint](https://github.com/docker-library/postgres/blob/master/docker-entrypoint.sh)
skips init scripts for an existing cluster. Never manually execute this fresh
init SQL against a restored/live database. PostgreSQL 18 PGDATA is under
`/var/lib/postgresql/18/docker`; the companion `/var/lib/postgresql/data` mount is
not a proxy for the actual cluster location. Both mounts may start empty here.

## Startup and optional recovery

Use `restore_waves` in the manifest as dependency order on the new host:
PostgreSQL/Redis, PgBouncer, Gluetun, Headscale, Headplane, Comet, then its namespace
consumers, infrastructure/apps, and update controllers last. Complete the normal
admin/enrollment bootstrap at the relevant stage. Apply `docker compose up -d
<services>` only after preflight on the prepared replacement host; wait for
running/healthy before starting dependents. No such startup was run in this audit.

Optional backups may preserve identities, existing users and warm caches. They
are an alternative branch of this process, not an input to the default flow.
For an explicit Jackett mixed-state recovery, `runtime-fields --snapshot-dir`
extracts only approved identity/session fields from a separate backup snapshot;
`render --mode restore` consumes that optional overlay. No old declarative secret
is a substitute for Infisical. Restore overlapping mount roots only once and
exclude `runtime_restore_exclusions` so restored old config cannot replace the
newly generated declarations. Never renew/remove volumes during optional recovery.

## Evidence limits

The prospective public tree is archived into an empty temporary directory.
All test secret/config inputs are synthetic; no live `.env`, `.secrets`, DB,
certificate, identity or volume is copied. All recovered templates are included,
without the four stub substitutes used by an earlier structural test.

The full source-readiness test archives only public files, generates synthetic
Infisical inputs, restores a synthetic stand-in for the sole required NPM backup,
imports synthetic PRIVATE_DR_PAYLOAD, checks all bind sources and validates
Compose without additional env-file flags. Native-code and fixture tests cover
AIO storage compatibility and NPM certificate association repair. Real provider
credentials, ACME issuance, application health and normal account/control-plane
enrollment are verified on the replacement host, not by starting live containers
in this audit. Source readiness is not a claim of a completed real recovery.

```sh
python3 -B -m unittest discover -s tests -p test_aio_dr.py
python3 -B tests/test_fresh_tree.py
```

Both tests use only synthetic inputs. The latter needs read-only Docker access
for Compose validation and mounted-target guards; it creates no containers.
