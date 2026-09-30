# Automatic public stack export

The live source is `/home/pi/streams-aio`. The destination is the existing public
repository `dav1dera/streams-aio-sanitized`. The installed worker lives separately
under `/home/pi/.local/share/streams-aio-public-sync/tooling`; its state and verified
commit receipt live under `/home/pi/.local/state/streams-aio-public-sync`, mode 0700.
The exporter never writes the live stack or copies its Git history.

## What follows the live stack

The exporter starts from the current public `main` tree, not from `git add -A` on
the live deployment. It reads the declared Compose file, reviewed service env
files, and only the application config destinations named by the existing DR
render plan. It exports:

- Existing public numeric/boolean settings and numeric ports, public env numeric
  settings and switches, and the reviewed log-level enum.
- Image tag/digest changes within each already-reviewed registry/repository.
  Running/stopped Compose containers are inspected using only service, image ID
  and configured image metadata. RepoDigests and platform are read from image
  metadata; both `image-lock.json` and the fresh-host override update together.
  This captures Watchtower image changes even when a Compose tag remains the same.
- Public application settings in the existing JSON/YAML/TOML templates. Bound
  private fields retain `@@INFISICAL:...@@` references. An embedded binding must
  preserve its exact reviewed public code/URI skeleton.

Private/empty env slots retain their public placeholders. Known application
runtime identity fields are omitted. Reviewed generic Honey defaults remain
generic. Live comments are never copied. Root `.env`, `.secrets`, `.generated`,
DBs, volumes, credentials, certificates, historical exports and unrelated live
files are never traversed or uploaded. The tool does not read Infisical auth
files, resolve Compose private interpolation, execute app code, start/recreate
containers or modify Infisical.

New services, keys, mounts, string settings, extension implementation changes,
and unclassified runtime fields require an explicit public template/mapping
review. They stop publication with a reviewed file/pointer and a status code,
without logging the value. Arbitrary scripts/docs changes are not copied from
the live directory: manage those source changes in this public repository.
This prevents a secret added to an apparently public file from automatically
becoming permanent public Git content. The existing image repository may receive
a new tag; a new image repository also needs review and its DR classification.

Every selected file and the image inventory are read twice to refuse a changing
export. Only generated, validated files are committed on top of current public
`main`, using the API token's normal write permission and `force=false`. Other
public files/history are preserved. No changes means no commit. A successful
write is read back and verified against SHA256 before the local receipt is marked
verified. The source directory and running containers remain unchanged.

## Install and check on docker-stack

Python 3.11+, `gh` and read-only Docker inspection access are required as pi.
`gh` must have Contents read/write access to **streams-aio-sanitized**, as well
as the separate private DR repo if the same credential is used for both. Do not
print tokens or raw diffs. The command without `--publish` performs API GETs and
local reads only; no public commit is made.

```bash
sudo apt-get install -y python3-venv
mkdir -p /home/pi/.local/share/streams-aio-public-sync
git clone --depth 1 https://github.com/dav1dera/streams-aio-sanitized.git \
  /home/pi/.local/share/streams-aio-public-sync/tooling
python3 -m venv /home/pi/.local/share/streams-aio-public-sync/venv
/home/pi/.local/share/streams-aio-public-sync/venv/bin/python -m pip install \
  -r /home/pi/.local/share/streams-aio-public-sync/tooling/scripts/requirements-public-sync.txt
/home/pi/.local/share/streams-aio-public-sync/venv/bin/python -B \
  /home/pi/.local/share/streams-aio-public-sync/tooling/scripts/sync-public-stack.py \
  --source /home/pi/streams-aio
```

If the worker clone already exists, update only that clone using
`git -C /home/pi/.local/share/streams-aio-public-sync/tooling pull --ff-only`.
Do not pull/reset/clean the live deployment to install this worker. A failure
must be resolved before enabling the timer; keep the source copies intact.

After `PUBLIC_SYNC_CHECK PASS`, install and test through systemd:

```bash
cd /home/pi/.local/share/streams-aio-public-sync/tooling
sudo install -m 644 systemd/streams-aio-public-sync.service /etc/systemd/system/
sudo install -m 644 systemd/streams-aio-public-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start streams-aio-public-sync.service
systemctl show streams-aio-public-sync.service -p Result -p ExecMainStatus
sudo systemctl enable --now streams-aio-public-sync.timer
sudo -v
./scripts/verify-public-sync-systemd.sh
```

Require `Result=success`, `ExecMainStatus=0`, `SYSTEMD_PUBLIC_SYNC_RUN PASS` and
`SYSTEMD_PUBLIC_SYNC_TIMER PASS`. The timer checks about every fifteen minutes,
with up to one minute of jitter. The sandbox makes the source/home read-only and
allows writes only to the separate state directory and private temporary space.
The worker requires pi's existing read permissions; it never changes ownership
or permissions of live application files. If a read is refused, investigate that
specific file's permissions without printing its contents.

View sanitized statuses with
`journalctl -u streams-aio-public-sync.service --since '1 hour ago' --no-pager`.
Update worker code using a fast-forward pull in the tooling directory and repeat
the check/systemd test. Do not enable two writers for the same public branch.

## Interrupted publication

`pending.json` records the intended public commit and generated file hashes
before updating `main`. If the response was lost but main points to that commit,
the next run verifies it and completes the receipt without replaying the write.
If the public branch moved, the read-back is corrupt, or the write never reached
GitHub, publication stops and retains the pending record for review. Never remove
it merely to suppress the error. Inspect only public commit/ref metadata to decide
the next step. No force push, history rewrite or automatic branch reset is used.

## Recovery points and limits

This is periodic **configuration** capture, not a backup of every application's
runtime data and not zero data loss. The private `streams-aio-dr` timer continues
on its existing weekly schedule, keeping five verified releases. Changes made
after its latest successful run are not yet in an encrypted backup. Its existing
AIOStreams/AIOMetadata exports are not updated by this public sync.

Recover private inputs from a verified DR release and choose a matching historical
public configuration commit; newer public configuration may need private mappings
that did not exist at the older encrypted snapshot. Public Git preserves old
configuration versions. The private local `current.json` receipt identifies the
last verified public commit, but is not yet embedded in private DR manifests.
A source sync passing is not evidence of a real restore or health of every app.
New application state still needs the classification and external input contracts
documented in `DISASTER-RECOVERY.md`.

Tests use a synthetic live tree built from every current reviewed template, with
fake private values, and simulated API/Docker transports. They cover masking,
unknown fields, source/image races, symlink rejection, image locks, concurrent
branch changes, corrupt read-back and lost write responses. Live source checks
and the actual systemd run must be completed on docker-stack before reporting this
automation as operational.
