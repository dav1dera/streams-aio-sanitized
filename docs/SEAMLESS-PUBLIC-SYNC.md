# Local configuration to public GitHub

The source of changes is `/home/pi/streams-aio`. The public destination is
`dav1dera/streams-aio-sanitized`. The independent worker publishes a clean tree
onto the current public `main`; it never pushes the live checkout's history or
applies GitHub configuration to running containers. `--seamless` enables the
discovery policy. Without it, the previous strict fixed-schema policy remains.

## Stable layout

| Local content | Public export |
| --- | --- |
| `docker-compose.yml` | Service additions/removals, ports, mounts, dependencies, public settings and variable references |
| `data/<service>/.env` and `.env.example` | Public settings, including new names and strings; private keys are omitted |
| `config/public/**`, `config/templates/**`, `data/<service>/public/**` | Explicit public text configuration, subject to content checks |
| Root text files, `docs/**`, `scripts/**` | New files and later local edits, subject to checks; the exporter's own controls remain owned by the public repo |
| Existing mixed app config destinations in the render plan | JSON/YAML/TOML/text templates with explicit Infisical bindings; public fields and extension implementation code can evolve |
| Bind directories | Paths, UID, GID and permissions needed to recreate empty directories; no directory contents |
| `.env`, `.secrets/**`, `.generated/**`, credentials, identities | Never published; only read privately when needed to recognize leaks/bindings |
| Other `data/<service>/**`, databases, caches, logs, archives, certificates, keys, backups | Excluded, including state files with unfamiliar names or no extension |

The public file types are Markdown, shell/Python/JavaScript/TypeScript, JSON,
YAML, TOML, INI/conf/cfg, plain text, SQL declarations, examples and templates.
Text-like names still fail if the contents are binary, database/archive headers,
SQL dump headers, private keys, recognized credential patterns, literal
credential assignments, or known private values. Additional source Git ignore
rules can exclude more files; negation cannot override the exporter's private
path/type floor. The runtime tree is not recursively inspected for public files.

Public examples may use the existing `INFISICAL:/folder/KEY` notation, canonical
`@@INFISICAL:/folder/KEY@@` markers or `${VARIABLE}` references. Credential fields
accept a complete reference, never a reference with an additional literal value.
JSON/YAML/TOML checks also parse `.example` and `.template` variants, including
inline objects. Python source is inspected as syntax, without import or execution:
reading credentials from runtime inputs and composing SQL with runtime values are
valid, while static passwords/API keys and SQL containing literal passwords remain
refused. Known-private-value, identity, token and default-value checks still apply.

The `.gitignore` rule set follows this layout for future services as well. The
installer preserves custom exclusions. Git ignore does not untrack existing
files: this installer never stages files, deletes application data or rewrites
history. The exporter's independent selection applies even to already tracked
local files. An arbitrary secret in an innocuously named public string cannot
always be recognized: keep private values in Infisical and public-only files in
the public directories.

## Adding a service

Use the same convention as the existing services:

```yaml
services:
  new-service:
    image: example/new-service:1
    env_file:
      - ./data/new-service/.env
      - ./.secrets/new-service.env
    volumes:
      - ./data/new-service/public/app.conf:/app/app.conf:ro
      - ./data/new-service/state:/app/state
```

Put only ordinary settings in the public `.env` and public config directory.
Have Infisical Agent render private variables to `.secrets/new-service.env`,
then apply the local service as usual. Naming an Infisical folder alone does not
configure the Agent: its destination/template must exist as for existing
services. The worker discovers the private target, but **never guesses a project,
folder or key mapping**. Unknown private targets are listed in
`config/public-stack-layout.json` as requiring the restored native Agent.

A new app config file mixing settings and secrets still needs an explicit
render-plan template/binding once. The worker refuses an unclassified private
bind file. Do not place the whole runtime directory under `public/`. A new app's
UI settings stored only in its database are intentionally not recovered.

The image must already be available locally with matching public RepoDigests.
The exporter reads only container service/image metadata and never pulls images
or starts containers. Image locks, the fresh-host override, dependencies,
service/reference inventory and empty directory layout update together. Changes
to the existing reviewed PostgreSQL recovery volume override require a review.
Private env keys are classified per consuming service, so an Agent setting for
one service cannot hide another service's public tuning. Public constants already
declared in the baseline are not treated as evidence of a credential leak.
An unchanged Compose healthcheck scalar already public at that same service and
field remains public even if a private input contains part of its local URL or
command. This approval is limited to that exact scalar and field: changed
healthchecks, new services, other fields, comments and other files still undergo
private-value checks. Credential patterns and literal credential assignments are
checked even in approved healthchecks; private values are not globally ignored.
Config rows are aligned by stable public IDs/names when available; reordering
does not move private bindings to another row. Removing a private binding or
changing an unidentified bound array still requires an explicit contract review.

## Removal and direct GitHub changes

Removing a service from local Compose removes its image lock, component and
unused reviewed templates/public env files and private references. Shared active
config and bindings remain. Local
runtime data is never deleted. New exported public files are recorded in
`config/public-sync-inventory.json`; later source deletions remove only those
owned files whose public digest still matches. An externally edited deletion
target stops publication.

The first run records source hashes for existing scripts/docs without replacing
newer GitHub copies with the older live checkout. After that, unchanged local
scripts/docs preserve direct GitHub edits; changed local files become the source
again. Compose, service settings and app templates always follow the local
source. Editing these directly on GitHub can be overwritten at the next run.
The exporter's controls, CI, units, policy and own guide stay owned by GitHub.

No raw private env hashes are published. Inventory hashes describe the sanitized
public inputs. File contents and image inventories are read twice to reject
changes during collection. Publication preserves concurrent-head checks,
non-force writes, SHA256 read-back and recovery from ambiguous write responses.
Errors print codes and paths, never configuration values or raw diffs. Unexpected
failures report only the exception class and line numbers in the two exporter
scripts; exception messages, source lines and frame locals are never printed.

Headplane and Headscale store live YAML configurations. Their reviewed public
templates intentionally use JSON, which is also valid YAML and remains compatible
with the existing recovery renderer. Export reads the live `.yaml`/`.yml` source
with the same safe YAML loader as the strict mode, masks its Infisical bindings,
and writes the public JSON template. Duplicate keys, aliases and unsafe YAML tags
remain refused; files declared as `.json` still require valid JSON.

Honey's existing dashboard has positional `SERVICES_<index>` Infisical bindings.
Its public `Service N` labels intentionally differ from private display names;
they are normalized before array matching and private-value collection. Reviewed
names, descriptions, public icons and placeholder links keep their canonical
public defaults. Bound links and icons retain their Infisical references, and
other public settings follow the local source. Honey row additions/removals and
changes to the positions of its named public anchors require explicit review
(`HONEY_BOUND_LAYOUT_REVIEW_REQUIRED`). Keep its positional bindings in sync when
reordering anonymized rows; their display labels are not stable identifiers.
Missing private fields remain refused. Other applications still match array rows
by their reviewed IDs or names; this exception applies only to Honey's template.

## Disaster recovery scope

Application databases can restart empty. Public files plus the empty-directory
manifest recover declarations and file-based settings. Infisical's encrypted
PostgreSQL/config backup and the native Agent/bootstrap backup are essential to
recover private configuration. New Agent mappings need a **new encrypted DR
backup**; an older snapshot cannot restore mappings created later. Public sync
does not trigger or change that separate weekly backup. Trigger it manually
after important private/bootstrap changes if a seven-day recovery window is too
long. NPM's existing encrypted backup and AIOStreams/AIOMetadata export behavior
remain unchanged. The older fresh-host guide still has its existing importer
prerequisites; this change does not claim a full restore drill or rewrite those
importers.

## Enable on docker-stack

First update only the independent worker and check without publication:

```bash
SYNC_HOME=/home/pi/.local/share/streams-aio-public-sync
git -C "$SYNC_HOME/tooling" pull --ff-only &&
"$SYNC_HOME/venv/bin/python" -B "$SYNC_HOME/tooling/scripts/sync-public-stack.py" \
  --source /home/pi/streams-aio --seamless
```

After `PUBLIC_SYNC_CHECK PASS`, install the source ignore policy and select the
new mode in the existing service:

```bash
"$SYNC_HOME/venv/bin/python" -B "$SYNC_HOME/tooling/scripts/install-public-sync-policy.py" \
  --source /home/pi/streams-aio &&
cd "$SYNC_HOME/tooling" &&
sudo install -m 644 systemd/streams-aio-public-sync.service /etc/systemd/system/ &&
sudo systemctl daemon-reload &&
sudo -v &&
./scripts/verify-public-sync-systemd.sh &&
systemctl is-active streams-aio-dr.timer
```

The verifier runs the service and checks both its result and active timer. The
15-minute public timer remains; encrypted DR retains its weekly schedule. Keep
the existing worker/state directories and authentication. Do not pull the public
repo into the live source or replace application directories with the worker.

## Local documentation and recovery guide

The deployment source remains authoritative for stack changes. GitHub does not pull the VM: its systemd worker publishes local changes every approximately 15 minutes. The initial unchanged-source protection may leave older local docs while preserving a newer GitHub edition.

To adopt the final guides locally, use [the README documentation-only update](../README.md#tenere-le-guide-locali-allineate). The helper backs up replaced local docs outside both checkouts and copies only its fixed guide/image inventory. It does not pull the public Compose into the running deployment. Subsequent local document edits use the normal export policy. The guide installer itself is reserved repository tooling.

The current single recovery runbook is [DISASTER-RECOVERY.md](DISASTER-RECOVERY.md). Verified backup/publication is separate from a complete replacement-host restore drill.
