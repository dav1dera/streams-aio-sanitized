# NPM: one database, newly issued TLS

The only mandatory runtime recovery artifact for this component is
`data/npm/data/database.sqlite`. Store a **consistent single-file SQLite
snapshot** in the separate encrypted DR layer. Do not commit it or import it
into Infisical. A raw copy while SQLite is writing is not a consistent snapshot;
if WAL is in use, use a supported SQLite backup/snapshot operation during the
separate backup job. No database snapshot was created by this audit.

The inspected database passed `PRAGMA integrity_check`, is 405,504 bytes (396 KiB),
is owned by numeric UID/GID 1000:1000 and is under the `/data` bind. Its existing
mode is `0674`; this audit did not change it. Restore the replacement artifact
with restrictive mode `0600`, preserving the intended numeric owner/group.
The size is the observed SQLite file size, not a promise about future encrypted
archive sizes. Its users, authentication records, routing, forwarding, access
lists and certificate metadata make it a sensitive artifact.

`data/npm/data/letsencrypt/**` is optional runtime state. The replacement may
start with an empty certificate directory. No certificate/private key, ACME
account or NPM JWT signing key is required from the old host. NPM may regenerate
its local JWT signing key; old login sessions then expire.

## TLS source of truth

The restored database supplies the single wildcard's domain names, certificate
owner/contact, provider settings and proxy associations. The canonical
`/shared/CLOUDFLARE_API_TOKEN` supplies the DNS credential. Its value was compared
read-only with NPM's current certificate metadata and matched. There is no
service-scoped duplicate and no Infisical Certificate Management integration.

NPM 2.16.0 uses its Certbot implementation to perform Let's Encrypt Cloudflare
DNS-01 issuance. The recovery helper calls that implementation inside the pinned
**replacement** NPM image. It does not substitute another certificate manager.
NPM writes the DNS credential file at issuance/renewal time. Legacy numbered
credential files and old TLS material are not required recovery inputs.

## Replacement-host-only sequence

Complete the generic fresh target initialization/rendering and create empty
writable directories as documented in `DISASTER-RECOVERY.md`. Restore only the
NPM SQLite artifact at its declared path, **before starting NPM**. Exclude all
old generated nginx configuration, logs and certificate trees from the default
flow.

```sh
sudo python3 -B scripts/npm-dr.py prepare --target "$PWD"
# Start NPM on this replacement host only, after ordinary Compose preflight.
docker compose up -d npm
sudo python3 -B scripts/npm-dr.py reissue --target "$PWD"
# After the upstream applications are running:
sudo python3 -B scripts/npm-dr.py verify --target "$PWD"
```

These commands were prepared, not run against the deployment. `prepare` refuses
any target already mounted by a container, even a stopped container. Every
operation requires both fresh-target and completed-render markers. `reissue`
and `verify` additionally require the exact Compose working directory, `/data`
and `/etc/letsencrypt` bind sources, pinned image digest and NPM version. Do not
forge a fresh marker on an existing deployment.

The preparation step records a private recovery journal under `.generated/`,
then quarantines the affected proxy hosts and certificate row in the restored
DB. It does not delete rows, relax SSL/HSTS, change forwarding or disable access
lists. This prevents missing TLS files from being treated as valid proxy
configuration and prevents the renewal timer from racing first issuance.

The reissue step keeps the restored certificate ID where possible. If that row
has disappeared, it selects one unambiguous matching certificate or creates a
replacement row. It obtains the resulting ID internally and updates all recorded
proxy/redirection/dead-host/stream associations automatically. It preserves the
original enabled state of each host. A valid existing key/certificate pair is
reused on retry, limiting unnecessary ACME requests.

After issuance, it validates the key/certificate pair and wildcard SAN, rebuilds
access-list files and nginx configuration from the DB using NPM's own models and templates, tests/reloads nginx
and restores the proxy enabled states. TLS probes verify the chain and server
name using loopback inside the replacement container, avoiding host/macvlan
reachability assumptions. The final `verify` also requests HTTPS for enabled
hosts; upstream 5xx responses fail, while redirects and access-policy 4xx
responses are accepted. Private names, tokens and library errors are suppressed.

The wrapper does not start/restart containers. Its future reissue operation
necessarily reloads nginx inside the replacement NPM container after successful
validation. No live reload or issuance was performed during preparation.

## Validation and limits

Offline tests cover quarantine/idempotence, live-target and mount refusal,
unchanged forwarding/access policy, successful issuance, unchanged/new certificate
IDs, association repair, retry without reissuance, failure without enabling
hosts, and TLS/HTTPS verifier logic. The relevant NPM implementation files were
compared byte-for-byte with the running image and the version-pinned upstream.
Network issuance, Cloudflare permissions, Let's Encrypt quota, DNS propagation
and actual replacement-host HTTPS remain unexecuted. Script readiness is not a
claim that a new certificate has already been issued.

Keep application ingress restricted until this procedure and first-administrator
bootstrap are complete. If issuance fails, keep the replacement in recovery;
do not drop the DB or overwrite it with a fresh empty NPM database.

Sources: [NPM Certbot DNS plugins](https://nginxproxymanager.com/certbot/),
[NPM 2.16.0 certificate implementation](https://github.com/NginxProxyManager/nginx-proxy-manager/blob/v2.16.0/backend/internal/certificate.js),
[NPM 2.16.0 regeneration logic](https://github.com/NginxProxyManager/nginx-proxy-manager/blob/v2.16.0/backend/scripts/regenerate-config).
