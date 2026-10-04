# Disaster recovery · guida operativa del sistema finalizzato

[← README](../README.md) · [Uso quotidiano ed esempi](ESEMPI-PRATICI.md)

Questa è la guida principale per ripartire su **host nuovi**. Le altre guide approfondiscono NPM e AIO senza definire un secondo percorso di ripristino. Il modello scelto è ricostruire lo stack dalle dichiarazioni e da Infisical: cache, sessioni e database applicativi ricostruibili possono ripartire vuoti.

**Backup e sincronizzazione sono operativi. Il ripristino completo su VM vuote non è ancora stato eseguito.** Non confondere la verifica SHA256 degli artifact con la prova che Infisical, i servizi e TLS si siano ripristinati correttamente. La procedura usa gli strumenti esistenti; i passaggi ancora dipendenti dal deployment privato sono indicati esplicitamente.

## 1. Cosa recuperare prima di iniziare

| Input | Dove si trova | Obbligo |
| --- | --- | --- |
| Compose, env pubblici, template, directory, digest e script | `dav1dera/streams-aio-sanitized` | Scegliere un commit pubblico coerente con il backup privato |
| Database e configurazione del server Infisical | Release nel repository privato `dav1dera/streams-aio-dr` | Essenziali per recuperare secret e impostazioni private |
| Agent, template e bootstrap privato | Artifact Agent della stessa release | Ripristinare i mapping applicati, compresi quelli aggiunti dopo l'installazione iniziale |
| Database NPM | Artifact NPM della stessa release | Ripristinare prima di avviare NPM |
| PRIMARY o RECOVERY age identity | Custodia esterna ai repository | Deve essere recuperabile senza le VM perdute |
| Accesso GitHub e bootstrap host | Custodia esterna | L'accesso al repo privato deve funzionare prima di recuperare Infisical |
| Export logici AIO e `/dr/AIO_PROFILE_BINDINGS` | Percorso privato AIO esistente e Infisical | Richiesti dal preflight completo attuale; non inclusi automaticamente nelle release DR |
| Procedura privata di installazione/ripristino Infisical e backup producer | Custodia operativa esterna | I producer installati fuori Git non vengono ricreati dal clone pubblico |

La release contiene esattamente questi sette file:

```text
npm-database.sqlite.age
infisical-agent-infisical-agent.tar.gz.age
infisical-config.tar.gz.age
infisical-postgres.dump.age
infisical-postgres-globals.sql.age
manifest.json
SHA256SUMS
```

Non sono backup di tutti i database dello stack. Il PostgreSQL **Infisical** si ripristina; il PostgreSQL **applicativo** dello stack può inizializzarsi vuoto. NPM conserva utenti, routing, access list e associazioni TLS tramite il proprio SQLite. Le vecchie chiavi/certificati NPM sono facoltativi.

Il `source_commit` della release identifica il codice del **repository DR privato**, non il commit dello stack pubblico. Non esiste ancora una coppia atomica tra commit pubblico e release privata. Registra il commit pubblico scelto nel tuo verbale di ripristino e verifica che i nuovi mapping siano presenti nel backup Agent scelto. Dopo modifiche importanti all'Agent avvia un backup privato immediato.

## 2. Scaricare e verificare la release

I blocchi dei passi 2 e 4–9 si eseguono sul **nuovo docker-stack**, con accesso GitHub autenticato, Git, Python e `age` installati; mantieni aperta la stessa shell per conservare le variabili. Il passo 3 riguarda l'altra VM Infisical: recupera lì gli artifact Infisical della stessa release e decifrali in storage privato locale, senza trasferire plaintext attraverso Git. Il materiale NPM/Agent resta disponibile sul nuovo docker-stack per i passi successivi.

Scegli una release pubblicata esplicita dalla [lista privata](https://github.com/dav1dera/streams-aio-dr/releases); non usare una bozza o il collegamento generico “latest”. Il tag qui sotto è un esempio realmente presente al controllo del 4 ottobre 2026: sostituiscilo con il punto di recupero scelto.

```bash
umask 077
DR_TAG=dr-20261004T023903Z
RESTORE_HOME="$HOME/dr-restore/$DR_TAG"
test ! -e "$RESTORE_HOME" &&
mkdir -p "$RESTORE_HOME/ciphertext" "$RESTORE_HOME/private" &&
gh release download "$DR_TAG" --repo dav1dera/streams-aio-dr \
  --dir "$RESTORE_HOME/ciphertext" &&
(cd "$RESTORE_HOME/ciphertext" && sha256sum -c SHA256SUMS)
```

Prosegui solo con tutti i checksum `OK`. Per verificare anche inventario esatto, manifest e struttura age usa il manager del repository privato in un clone separato, fuori dallo stack. Il comando `remote-snapshot` esegue `bundle_check` prima di mostrare soltanto il timestamp Infisical; non pubblica, non elimina e non decifra nulla:

```bash
git clone --depth 1 --branch main https://github.com/dav1dera/streams-aio-dr.git \
  "$RESTORE_HOME/manager" &&
python3 -B "$RESTORE_HOME/manager/scripts/dr-releases.py" remote-snapshot \
  "$RESTORE_HOME/ciphertext" &&
python3 - "$RESTORE_HOME/ciphertext/manifest.json" "$DR_TAG" <<'PY'
import json, sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text())
if sys.argv[2] != 'dr-' + manifest['run']:
    raise SystemExit('DR_TAG_MANIFEST_MISMATCH')
print('DR_TAG_MANIFEST PASS')
PY
```

Atteso: controllo concluso senza `DR_RELEASES FAIL` e `DR_TAG_MANIFEST PASS`. La verifica controlla gli artifact locali; conserva il tag scelto e l'SHA del codice privato per il verbale di recupero.

Decifra **fuori da entrambi i clone**, usando il percorso della tua identity già recuperata. Non copiare il testo della chiave nel comando:

```bash
read -r -p 'Percorso della age identity esterna: ' AGE_IDENTITY
test -r "$AGE_IDENTITY" &&
age --decrypt --identity "$AGE_IDENTITY" \
  --output "$RESTORE_HOME/private/database.sqlite" \
  "$RESTORE_HOME/ciphertext/npm-database.sqlite.age" &&
age --decrypt --identity "$AGE_IDENTITY" \
  --output "$RESTORE_HOME/private/infisical-agent.tar.gz" \
  "$RESTORE_HOME/ciphertext/infisical-agent-infisical-agent.tar.gz.age" &&
age --decrypt --identity "$AGE_IDENTITY" \
  --output "$RESTORE_HOME/private/infisical-config.tar.gz" \
  "$RESTORE_HOME/ciphertext/infisical-config.tar.gz.age" &&
age --decrypt --identity "$AGE_IDENTITY" \
  --output "$RESTORE_HOME/private/infisical-postgres.dump" \
  "$RESTORE_HOME/ciphertext/infisical-postgres.dump.age" &&
age --decrypt --identity "$AGE_IDENTITY" \
  --output "$RESTORE_HOME/private/infisical-postgres-globals.sql" \
  "$RESTORE_HOME/ciphertext/infisical-postgres-globals.sql.age"
```

La decifratura verifica anche l'integrità autenticata age. In caso di errore conserva gli artifact cifrati e analizza il problema; non caricare i file decifrati o i log privati su GitHub.

## 3. Ripristinare prima Infisical

Ripristina su una VM nuova la configurazione inclusa nell'archivio Infisical, mantenendo le **stesse chiavi applicative di cifratura/signing**, la versione compatibile dell'applicazione e il PostgreSQL compatibile con il dump. Un database restaurato con chiavi applicative nuove non basta per recuperare i secret.

L'ordine operativo è: configurazione privata → PostgreSQL isolato → globals/ruoli compatibili → database dal dump → applicazione Infisical → controllo GUI e autenticazione Agent. Ripristina ruoli e proprietà secondo il deployment recuperato, gestendo esplicitamente i ruoli già esistenti nel cluster nuovo; non eseguire alla cieca un dump globals su un database in uso.

**Questo repository non contiene un restore runner Infisical provato.** Non sappiamo dai soli artifact cifrati quali percorsi interni, volumi, nomi di servizio e database usi l'archivio. Non viene quindi proposto un comando `tar` verso `/` o un `pg_restore` con nomi inventati. Segui il runbook privato del deployment Infisical e registra questi dati prima di dichiarare conclusa una prova di ripristino.

Controllo di riuscita: la GUI vede progetto/ambiente/cartelle recuperati, l'Agent si autentica e un template campione viene renderizzato senza stampare valori. Il solo stato “running” del container Infisical non soddisfa questo controllo.

Recupera poi l'Agent nativo e i suoi template/bootstrap dall'archivio Agent, mantenendo autenticazione e permessi privati. Non attivare subito i vecchi hook `execute.command` contro uno stack ancora in costruzione: controlla prima percorsi e destinazioni sul nuovo host. I mapping nuovi si recuperano dall'Agent cifrato, non vengono indovinati dall'esportatore pubblico.

## 4. Preparare un clone pubblico vuoto

Questa parte si esegue solo sul nuovo `docker-stack`, mai sul deployment già in uso. Il riferimento attuale usa utente `pi`, UID/GID 1000 e `/home/pi/streams-aio`. Adatta l'host senza riutilizzare nomi di container/volumi della produzione durante una prova isolata.

Prerequisiti: Docker Engine e Compose funzionanti, Git, `gh`, Python 3.11+, `venv`, strumenti ACL, Infisical CLI/Agent compatibile. Le immagini del lock devono essere disponibili per l'architettura scelta.

```bash
sudo apt-get install -y git gh python3-venv acl &&
test ! -e /home/pi/streams-aio &&
git clone https://github.com/dav1dera/streams-aio-sanitized.git /home/pi/streams-aio &&
python3 -m venv /home/pi/.local/share/streams-aio-dr-tools/venv &&
/home/pi/.local/share/streams-aio-dr-tools/venv/bin/python -m pip install \
  -r /home/pi/streams-aio/scripts/requirements-dr.txt
```

Prima di creare file privati, seleziona nel clone nuovo il commit pubblico scelto. Registra l'SHA (`git rev-parse HEAD`); il branch `main` corrente non costituisce da solo un punto di recupero coerente con una vecchia release.

```bash
cd /home/pi/streams-aio
DR_PY=/home/pi/.local/share/streams-aio-dr-tools/venv/bin/python
"$DR_PY" -B scripts/dr.py init --target "$PWD"
```

Atteso: `FRESH_TARGET INITIALIZED`. `init` rifiuta file privati, configurazioni o runtime già presenti. Non creare a mano marker per aggirare il rifiuto.

## 5. Fornire gli input Infisical al renderer

Il renderer iniziale usa snapshot privati `.secrets/dr-inputs/*.goenv`, distinti dai file env di servizio dell'Agent nativo. Fornisci progetto, ambiente, endpoint e **percorsi** dei file Universal Auth recuperati; i valori di client ID/secret non devono essere argomenti CLI o variabili scritte nelle guide.

```bash
"$DR_PY" -B scripts/dr.py agent-config --target "$PWD" \
  --project-id "$INFISICAL_PROJECT_ID" --environment "$INFISICAL_ENVIRONMENT" \
  --address "$INFISICAL_ADDRESS" \
  --client-id-file "$CLIENT_ID_FILE" --client-secret-file "$CLIENT_SECRET_FILE"
```

Atteso: `AGENT_CONFIG PREPARED`. Il file `.generated/dr-agent.json` è privato. Eseguilo con la modalità `agent` supportata dalla CLI installata e con i permessi del gruppo deployment; verifica prima l'help della **versione recuperata**. Il generatore del repo usa la funzione template `secret`, mentre versioni più recenti documentano `listSecrets`: non sostituire automaticamente una sintassi con l'altra. Il bootstrap temporaneo non ha hook di ricreazione e non scrive direttamente configurazioni possedute dalle app.

Attendi tutti gli input dichiarati nel [piano](../config/dr-render-plan.json), inclusi l'allowlist email esplicita di OAuth2 Proxy e, per il percorso AIO, i binding profili. Non usare valori vuoti o wildcard per far passare un controllo. Se il nuovo stack contiene target Agent aggiunti dopo il piano iniziale, ripristinali dal mapping nativo e integra il relativo contratto prima del preflight completo.

## 6. Generare configurazioni e stato vuoto

```bash
sudo "$DR_PY" -B scripts/dr.py render --target "$PWD" --mode fresh &&
sudo "$DR_PY" -B scripts/dr.py prepare-state --target "$PWD"
```

Attesi: `RENDER PASS` e `EMPTY_STATE_DIRECTORIES PASS`. Il rendering valida gli output prima di scrivere e non sovrascrive configurazioni esistenti. Honey, Headscale/Headplane, Jackett e Seanime vengono inizializzati una volta; l'Agent non deve sovrascrivere continuamente questi file modificabili dalle applicazioni.

Il renderer crea `.env` con `COMPOSE_FILE=docker-compose.yml:config/compose.dr.yaml`: i normali comandi Compose useranno l'override DR e i digest del lock. Non creare la `.env` copiando un esempio vuoto. Il layout pubblico registra directory **montate**, non tutte le cartelle arbitrarie: una cartella vuota `data/test` senza mount non appare su GitHub.

Il PostgreSQL applicativo riceve SQL di bootstrap per i database/ruoli **già dichiarati nel piano**. Password e verifier PgBouncer devono combaciare; gli script non deducono il database di un nuovo servizio. PostgreSQL esegue l'init solo su un cluster vuoto. Non applicare il fresh init SQL su un PostgreSQL restaurato o in produzione.

## 7. Recuperare NPM e gli input AIO previsti

Ripristina il solo SQLite NPM, con NPM ancora spento sul nuovo host:

```bash
test ! -e data/npm/data/database.sqlite &&
sudo install -m 600 -o 1000 -g 1000 "$RESTORE_HOME/private/database.sqlite" \
  data/npm/data/database.sqlite &&
sudo "$DR_PY" -B scripts/npm-dr.py prepare --target "$PWD"
```

Atteso: `NPM DATABASE_PREPARED PASS`. UID/GID devono essere quelli del deployment recuperato; il valore 1000 è il riferimento attuale. [NPM-RECOVERY.md](NPM-RECOVERY.md) spiega il resto della sequenza e i limiti di versione del helper.

Il preflight completo attuale richiede il payload logico AIO e il suo marker: **non è facoltativo in questo percorso**, anche se gli SQLite applicativi non vengono copiati dalla vecchia VM. Prepara il payload decifrato fuori dal clone, assegna a `AIO_DR_PAYLOAD` il suo percorso e segui il contratto/versioni di [AIO-EXPORT-IMPORT.md](AIO-EXPORT-IMPORT.md).

```bash
"$DR_PY" -B scripts/aio-dr.py validate --target "$PWD" --payload "$AIO_DR_PAYLOAD" &&
sudo "$DR_PY" -B scripts/aio-dr.py import --target "$PWD" --payload "$AIO_DR_PAYLOAD" &&
sudo "$DR_PY" -B scripts/dr.py preflight --target "$PWD" --mode fresh &&
docker compose --profile all config --quiet
```

Atteso: `DR_PREFLIGHT PASS` e validazione Compose senza errori. Gli importatori AIO e il helper NPM sono legati a versioni applicative precise: il digest autoaggiornato non è prova automatica della compatibilità del loro codice. Non cambiare versione durante il recupero senza verificarne il contratto. Saltare gli importatori o falsificare i marker non costituisce un preflight riuscito.

## 8. Avviare per dipendenze e verificare

Consulta `restore_waves` nel [manifest](../config/dr-manifest.yaml) per i servizi presenti nel commit scelto, ma lascia **Watchtower e altri controller di aggiornamento per ultimi**. Non lanciare tutto indiscriminatamente: completa bootstrap/enrollment al momento necessario e aspetta i servizi richiesti dai dipendenti.

Una prima base sul nuovo host è:

```bash
docker compose --profile all up -d postgres redis &&
docker compose --profile all ps postgres redis
```

Verifica disponibilità del database e health prima di PgBouncer/app; prepara Gluetun prima dei servizi nel suo namespace. Headscale vuoto richiede utenti, enrollment e nuove API/preauth key; Headplane richiede una API key valida e il primo owner. Le stringhe di vecchie chiavi presenti in Infisical non ricreano quelle registrazioni nel database nuovo. Mantieni accessi amministrativi limitati durante il bootstrap.

NPM segue la sua sequenza: avvio → riemissione TLS → upstream disponibili → verifica HTTPS. Le dipendenze esterne Cloudflare/ACME e i limiti di rilascio certificati possono incidere sul tempo di recupero. Portainer, TeamSpeak, VPN e provider possono richiedere una normale prima registrazione. Cache, sessioni, cronologie e impostazioni salvate soltanto nei database non inclusi ripartono da zero.

Controllo di riuscita: servizi attesi running/healthy, accessi amministrativi validi, routing e HTTPS NPM verificati, secret distribuiti, applicazioni raggiungibili. “Compose valido” non significa “stack funzionante”.

## 9. Riattivare i due processi automatici

Solo dopo il controllo del nuovo deployment:

1. Installa il worker pubblico secondo [README](../README.md#attivare-la-sincronizzazione), con source locale e `--seamless --publish`.
2. Installa il repository privato, i producer esterni, accesso SSH/sudo noninterattivo e recipient **pubblici** age secondo il [README privato](https://github.com/dav1dera/streams-aio-dr/blob/main/README.md). Le private identity restano esterne. La migrazione dei vecchi snapshot Git è una procedura storica, non un prerequisito di ogni nuova installazione.
3. Esegui i verificatori dei due servizi e controlla timer, risultati e una release nuova riletta con successo.

```bash
systemctl list-timers streams-aio-public-sync.timer streams-aio-dr.timer --no-pager --full
systemctl show streams-aio-public-sync.service streams-aio-dr.service \
  -p Id -p Result -p ExecMainStatus -p ExecMainExitTimestamp
```

Per i oneshot `inactive` dopo la fine è normale; attesi `Result=success`, `ExecMainStatus=0` e timer attivi. Pubblico: circa 15 minuti. Privato: domenica 04:30–04:40 nel fuso dell'host, massimo 5 release gestite. Non cancellare il vecchio punto di recupero o materiale privato prima di aver concluso la verifica.

## Stato delle verifiche e ultima prova necessaria

| Controllo | Evidenza disponibile |
| --- | --- |
| VM: sincronizzazione seamless e timer pubblico | Esecuzione reale riuscita e commit riletti su GitHub |
| VM: backup privato e systemd | Migrazione/upload verificati e servizio riuscito; release `dr-20261004T023903Z` pubblicata con 7 asset al controllo del 04/10/2026 |
| Controlli pubblicazione, retention e preparazione | Test con fixture sintetiche; non usano secret reali |
| Rendering fresh, AIO e NPM | Test separati degli strumenti; non equivalgono a un ripristino reale completo |
| Restore Infisical + Agent + servizi + ACME su host nuovi | **Non ancora eseguito** |

Per eliminare gli ultimi punti che potrebbero richiedere tentativi, esegui una prova su due VM isolate con una release reale: documenta i percorsi/servizi del restore Infisical, verifica la sintassi dell'Agent recuperato, gli input AIO realmente disponibili e la compatibilità delle immagini con i helper. Conserva tempi/esiti e commit/tag usati. Solo quella prova consente di indicare un tempo di recupero misurato e di chiamare il percorso completo “provato da zero”.
