# AIO · contratto di export/import logico esistente

[← Procedura unica DR](DISASTER-RECOVERY.md)

AIOStreams/AIOMetadata mantengono il percorso precedente, senza modifiche al codice o al flusso applicativo in questo aggiornamento. I loro SQLite sono opzionali come backup; il percorso completo attuale ricostruisce database nuovi da payload logici privati e richiede il marker dell'importazione AIO nel preflight.

Le release automatiche `streams-aio-dr` **esistono già**, ma contengono soltanto NPM, Agent e Infisical. Non contengono automaticamente questi payload AIO. Conserva gli export separatamente cifrati e recuperabili prima di perdere l'host originale; disponibilità degli export e di `/dr/AIO_PROFILE_BINDINGS` va verificata, non presunta.

L'adapter resta legato ad AIOStreams 2.35.3 e AIOMetadata 3.3.0. L'aggiornamento automatico dei digest delle immagini pubbliche non prova la compatibilità di una versione successiva con questo importer. Non aggirare i controlli di versione e non falsificare il marker.

The public repository contains the importer, schemas, examples and private-field
mapping contract. A DR-ready consumer does not imply the private inputs already
exist. Before retiring an original host, obtain exports through the applications'
authorized export workflows and securely provide the inputs described here.

## What the official workflows preserve

**AIOStreams 2.35.3:** export contains a `UserData` object. The full export may
contain credentials; never commit it. The official UI import drops `uuid` and
`trusted`, and normal user creation generates a new UUID. Config profile
owner/alias rows and linked-account records are outside that export.

**AIOMetadata 3.3.0:** export contains `version`, `config` and `metadata`. Even a
full export omits legacy manager secrets. OAuth/token/key IDs can refer to
separate records absent on a new instance. The save API accepts `userUUID` but
can also update existing records; it is not a safe fresh-only import guard.

Therefore `scripts/aio-dr.py` is an explicitly **custom, version-pinned storage
adapter**, not a claim that the ordinary UI imports preserve every identity.
It initializes NEW SQLite files from logical configurations before starting the
applications. It uses the pinned schemas, bcrypt settings and AIOStreams storage
codec; the applications run their remaining normal migrations on first start.
It never opens a source database and refuses any existing target database.
Do not change the pinned application images without retesting the adapter.

## Inputs and separation

| Input | Where | Purpose |
| --- | --- | --- |
| Official logical export bundle | External private file, mode 0600 | User settings and credentials present in the official exports |
| `/dr/AIO_PROFILE_BINDINGS` | Infisical, JSON string | Stable configuration UUIDs, passwords, optional profile ownership/aliases and AIOMetadata trust |
| Existing service/shared paths | Infisical | Deployment secrets and explicit per-field bindings |
| `/aiostreams/SECRET_KEY` | Existing Infisical key | Existing encrypted-password URLs remain usable with the same password and UUID |
| `/aiostreams/TRUSTED_UUIDS` | Existing service environment when configured | AIOStreams trust policy; never promoted from an untrusted export flag |

`/dr/AIO_PROFILE_BINDINGS` is a declared private input; the backup/publishing job does not create it. Verify that it exists in the recovered Infisical project.
Existing canonical secrets may be referenced with `{"ref":"/shared/KEY_NAME"}`
instead of duplicated. Other referenced paths must be among the folders rendered
by `config/dr-render-plan.json`; add an explicit input folder if needed.

The registry has two objects, `aiostreams` and `aiometadata`, keyed by stable,
non-personal profile labels such as `primary`. Each entry has `uuid` and
`password`. Optional AIOStreams `profile` has `id`, `owner`, `label` and `alias`;
optional AIOMetadata `alias` and `trusted` preserve their declared behavior.
These values belong in Infisical, never the public example. The importer requires
exactly the same profile label set in the payload and registry, unique UUIDs,
valid passwords, supported schemas and exact version matches.

`config/aio-bindings.schema.json` and `config/aio-payload.schema.json` are the
contracts. The `.example.json` files are **non-executable skeletons**: blank
identity values and replacement markers deliberately fail validation.

## Bundle construction without database extraction

Wrap the official exports into the external JSON bundle:

- `schema: 1`.
- `aiostreams`: one entry per exported configuration.
- `aiometadata`: one entry per exported user configuration.
- Each entry declares `profile`, exact `version`, the complete official `export`,
  and arrays `bindings`, `dependencies`, `reauthorize`.
- AIOStreams also declares `linked_accounts: "reauthorize"`. Reconnect these
  accounts through the normal application workflow; no session DB is restored.

A `bindings` entry specifies a JSON `pointer`, canonical Infisical `ref`, and
optional `encoding` (`string` or `json`). Pointers are relative to the actual
configuration, not AIOMetadata's export envelope. They can supply API keys or
manager credentials omitted by the official export. Missing values or parent
objects fail; the importer does not invent configuration structure.

A `dependencies` entry specifies `pointer`, target `service`, target `profile`
and `field` (`uuid`, `password` or `alias`). It resolves parent links and
cross-profile references from the same registry automatically. AIOStreams parent
UUID/password pairs must point to imported profiles. No UUID is regenerated:
existing addon URLs, Seanime extension URLs, aliases and trusted UUID policy
continue to reference the same IDs. An exported UUID that conflicts with the
registry is refused. Keep the original SECRET_KEY and configuration passwords;
existing encrypted-password URL blobs then remain valid without copying their
ciphertext out of a database.

For non-exported OAuth/linked-key records, `reauthorize` explicitly names a
provider and `remove_pointers` for unavailable references and dependent config.
There is no silent stripping. Unresolved `*TokenId`/`keyId` references fail. A
manager destination whose secret was omitted must have its `apiKey` bound from
Infisical or be explicitly removed for later reauthorization. No token/session
state is extracted from a DB or migrated into Infisical by this importer.

## Replacement-host command order

Install Python dependencies from `scripts/requirements-dr.txt` in the replacement
host's chosen Python environment (Python 3.11+). Commands below assume these
packages are available to the interpreter used by sudo; if using a virtualenv,
pass its absolute Python executable to sudo instead of `python3`. Complete `dr.py init`, Agent
snapshot rendering, `dr.py render`, and `dr.py prepare-state` first. The Agent
renders `/dr` to a private snapshot; it never writes AIO runtime databases.

Supply the decrypted bundle **outside the public clone**, mode 0600, preferably
in private temporary storage. Decryption belongs to the existing separate private AIO recovery path, outside Git. Do not supply values on command lines.

Continue the unique DR procedure with its `DR_PY` absolute virtualenv interpreter:

```bash
"$DR_PY" -B scripts/aio-dr.py validate --target "$PWD" --payload "$AIO_DR_PAYLOAD"
sudo "$DR_PY" -B scripts/aio-dr.py import --target "$PWD" --payload "$AIO_DR_PAYLOAD"
```

The importer checks the resolved Compose image, database URI and bind layout;
refuses targets mounted by any current/stopped container; requires fresh/render
markers; refuses symlinks and preexisting DBs; builds private databases before
installing them with no-replace links. New DB files use numeric 1000:1000 and
0600. A successful marker records only counts, versions and reauthorization
labels. A second import refuses rather than overwriting any database. If an
interrupted import leaves files, do not force a retry over them: use a new empty
replacement target. No container is started by the script.

Restore/prepare the sole NPM database artifact, run `dr.py preflight` and Compose
validation, then start the replacement stack in the documented dependency order.
Complete the declared provider/account reauthorizations during normal first use.

## Verification scope

Tests use synthetic exports and Infisical inputs, never live DB values. They
cover schema/version refusal, Infisical override application, stable IDs,
parent password links, aliases, trust records, explicit reauthorization,
SQLite integrity, no-overwrite/mounted-target guards and an independent native
Node decode of the pinned AIOStreams encryption format. The fresh-tree test
imports into an archived public tree and validates Compose. No production API
call, live export, real credential decryption or container startup is involved.

Sources: [AIOStreams UI export/import](https://github.com/Viren070/AIOStreams/blob/v2.35.3/packages/frontend/src/components/menu/save-install.tsx),
[AIOStreams user storage](https://github.com/Viren070/AIOStreams/blob/v2.35.3/packages/core/src/db/repositories/users.ts),
[AIOStreams crypto codec](https://github.com/Viren070/AIOStreams/blob/v2.35.3/packages/core/src/utils/crypto.ts),
[AIOMetadata export](https://github.com/cedya77/aiometadata/blob/c5015eccca247e11270656fa8cbf690e3fb6b974/configure/src/lib/exportConfigFile.ts),
[AIOMetadata storage](https://github.com/cedya77/aiometadata/blob/c5015eccca247e11270656fa8cbf690e3fb6b974/addon/lib/database.ts),
[AIOMetadata save API](https://github.com/cedya77/aiometadata/blob/c5015eccca247e11270656fa8cbf690e3fb6b974/addon/lib/configApi.js).
