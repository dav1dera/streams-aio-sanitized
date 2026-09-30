# Fresh-deployment prerequisites

Current readiness and the repository-contained preparation tools are described
in [DISASTER-RECOVERY.md](DISASTER-RECOVERY.md). The source consumer is verified using synthetic inputs. This does not mean real
private payloads or newly declared Infisical keys have already been provisioned.


This repository supplies public Compose declarations, service environment
templates and field mappings. It is not a state backup or a complete
one-command installer. No checkout of the previous private repository is
required. The following inputs must be provisioned separately:

- An Infisical project/environment containing the required private values,
  an authenticated renderer and its private bootstrap credentials.
- The root `.env` variables listed in `.env.example`, plus the service files
  named in Compose under `.secrets/`. Preserve the public-first/private-second
  environment-file order. Never commit the rendered values.
- Generated declarative files referenced by Compose under `.generated/`,
  with ownership and permissions appropriate for each consuming process.
- The reviewed skeletons and extension implementations in `config/dr-templates/`.
  Recovered template security reviews are complete. Supply the required private inputs before deployment. Application identities
  remain outside the repository.

`config/private-config-overlays.json` identifies the approved private fields
and their Infisical destinations for the mixed configuration files. It is a
field-mapping description, not an executable bootstrap tool. It does not
contain credentials or an application-state backup.

## Required one-shot bootstrap behavior

Honey, Headscale/Headplane, Jackett and Seanime can own or rewrite their local
configuration. Do not configure a continuous renderer to overwrite those live
files. The bundled one-shot initializer must:

1. Require explicit fresh skeleton and destination paths. Reject live runtime
   directories as sources and reject identical source/destination paths.
2. Refuse the entire operation if any destination configuration exists,
   including a symlink, before authentication or writes.
3. Resolve only approved declarative fields. Do not import runtime identity,
   sessions, indexer hidden data, databases or certificates into Infisical.
4. Validate all outputs before creating files, reject symlink parents and use
   exclusive file creation with no symlink following. Never overwrite files.
5. Apply explicit ownership, permissions and required ACLs for each process.

`scripts/dr.py` provides the fresh-target guard and one-shot rendering. It
refuses missing private inputs and invalid allowlists. It never starts
containers or overwrites initialized application configuration.

## Validation scope

After provisioning real private inputs, validate with:

```sh
docker compose --profile all config --quiet
```

The publication audit uses a temporary copy and synthetic private inputs for
Compose schema/interpolation validation. That test does not prove a fresh
application startup or the availability of real credentials. Never initialize
or replace existing state merely to validate these configuration templates.

## Quick redeploy is the default

Use `scripts/dr.py render --mode fresh`, followed by `prepare-state` and
`preflight`. Empty bind directories and automatically created Docker volumes are
normal inputs. Comet/CometNet caches, Redis sessions, rebuildable indices and
optional runtime identities do not require backups. Do not run these commands
on an initialized deployment.

The manifest separates REQUIRED_USER_STATE from OPTIONAL_RUNTIME_STATE and
DISPOSABLE_STATE. NPM's database is the only required runtime backup artifact;
restore it before NPM startup, then use `npm-dr.py` to reissue TLS and restore
proxy associations. No existing TLS or node identity is mandatory.

AIOStreams/AIOMetadata use logical configuration export/import with private
bindings from Infisical. Their SQLite files are optional. Supply PRIVATE_DR_PAYLOAD outside this clone
and run the fresh-only importer; no source DB extraction is required. Fresh Headscale/Tailscale
enrollment and administrator setup are normal first-use procedures. See the
DR guide and the NPM/AIO-specific procedures for exact scope and consequences.
