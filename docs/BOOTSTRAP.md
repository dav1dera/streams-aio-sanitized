# Fresh-deployment prerequisites

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
- Fresh upstream application configuration skeletons. Headscale and Headplane
  examples are under `config/templates/`; their `INFISICAL:` references are
  placeholders requiring resolution, not syntax understood by the apps.
- Any separately maintained Seanime extension implementations and their
  compatible configuration skeletons. Extension code and existing application
  identities are intentionally not distributed here.

`config/private-config-overlays.json` identifies the approved private fields
and their Infisical destinations for the mixed configuration files. It is a
field-mapping description, not an executable bootstrap tool. It does not
contain credentials or an application-state backup.

## Required one-shot bootstrap behavior

Honey, Headscale/Headplane, Jackett and Seanime can own or rewrite their local
configuration. Do not configure a continuous renderer to overwrite those live
files. A separately provisioned one-shot initializer must:

1. Require explicit fresh skeleton and destination paths. Reject live runtime
   directories as sources and reject identical source/destination paths.
2. Refuse the entire operation if any destination configuration exists,
   including a symlink, before authentication or writes.
3. Resolve only approved declarative fields. Do not import runtime identity,
   sessions, indexer hidden data, databases or certificates into Infisical.
4. Validate all outputs before creating files, reject symlink parents and use
   exclusive file creation with no symlink following. Never overwrite files.
5. Apply explicit ownership, permissions and required ACLs for each process.

The retained migration wrapper delegates these checks to its Python worker;
checks need not be duplicated in the shell wrapper. That deployment-specific
wrapper is intentionally not shipped as a portable installer. Its use is
never a prerequisite for an already initialized deployment.

## Validation scope

After provisioning real private inputs, validate with:

```sh
docker compose --profile all config --quiet
```

The publication audit uses a temporary copy and synthetic private inputs for
Compose schema/interpolation validation. That test does not prove a fresh
application startup or the availability of real credentials. Never initialize
or replace existing state merely to validate these configuration templates.
