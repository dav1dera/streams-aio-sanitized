# Esempi pratici · modificare in locale e conservare su GitHub

[← Torna al README](../README.md)

Gli esempi usano il deployment `/home/pi/streams-aio` e l'utente `pi`. Il worker e i timer sono già attivi sul deployment di riferimento. I comandi di installazione nel README servono per un'installazione nuova.

**La sequenza abituale è: modifica locale → applicazione del servizio → controllo → pubblicazione automatica.** Per una modifica privata importante, aggiungi un backup DR manuale. L'esportatore non avvia container e non aggiorna il deployment dalle modifiche GitHub.

## Aggiungere Comet da zero

Nel deployment attuale Comet esiste già: questa è una procedura didattica per capire come aggiungere un servizio equivalente. Non sostituire i file del Comet esistente con l'esempio ridotto. Per la configurazione effettiva consulta [Compose](../docker-compose.yml), [env pubblico Comet](../data/comet/.env) e [piano DR](../config/dr-render-plan.json).

### 1. Preparare la cartella e le impostazioni pubbliche

Per un Comet che non esiste ancora:

```bash
cd /home/pi/streams-aio
mkdir -p data/comet/data
```

Crea `data/comet/.env` con queste impostazioni iniziali:

```dotenv
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=2020
FASTAPI_WORKERS=1
DATABASE_TYPE=postgresql
LIVE_TORRENT_CACHE_TTL=86400
BACKGROUND_SCRAPER_ENABLED=false
COMETNET_ENABLED=false
```

Il file pubblico contiene preferenze, numeri e flag. Il file runtime `data/comet/data/public_api_token.txt`, eventuali database e cache appartengono allo stato privato escluso. La pubblicazione dei nomi delle directory non pubblica il loro contenuto.

### 2. Creare i secret dalla GUI Infisical

Apri il progetto e l'ambiente già usati dall'Agent. Prepara le chiavi necessarie, senza copiarne i valori nel Compose o nell'env pubblico:

| Sorgente Infisical | Variabile consumata da Comet | Scopo |
| --- | --- | --- |
| `/comet/DATABASE_URL` | `DATABASE_URL` | Connessione al database applicativo |
| `/comet/ADMIN_DASHBOARD_PASSWORD` | `ADMIN_DASHBOARD_PASSWORD` | Accesso al pannello amministrativo |
| `/comet/CONFIGURE_PAGE_PASSWORD` | `CONFIGURE_PAGE_PASSWORD` | Protezione della pagina di configurazione |
| `/shared/COMET_PUBLIC_API_TOKEN` | `PUBLIC_API_TOKEN` | Token condiviso, con alias nel file privato Comet |

Le ultime tre sono opzioni da usare secondo la configurazione scelta. Aggiungi le altre integrazioni solo quando ti servono. I valori e le eventuali URL personali restano in Infisical.

`DATABASE_URL` deve usare il formato accettato dalla versione di Comet installata; il piano attuale gestisce anche la forma senza prefisso del protocollo. Le componenti utente/password di una connessione devono essere codificate correttamente come componenti URI.

Una nuova password salvata su Infisical **non modifica automaticamente la password di un ruolo PostgreSQL già esistente**: server, connessione Comet ed eventuale autenticazione PgBouncer devono essere aggiornati insieme.

### 3. Collegare l'Agent al nuovo servizio

Questa parte si configura una volta sulla VM. Usa la configurazione e il percorso di template già adottati dall'Agent installato. Mantieni la sua autenticazione esistente e le credenziali fuori dal repo.

Il collegamento da realizzare è:

```text
Infisical /comet/* ───────────────┐
                                 ├── template privato ──> .secrets/comet.env
/shared/COMET_PUBLIC_API_TOKEN ───┘       alias: PUBLIC_API_TOKEN
```

Duplica un template env già funzionante per un servizio simile e adatta cartella, nomi e alias. Conserva il metodo di serializzazione dei valori: virgolette, ritorni a capo e `$` non vanno concatenati come semplice testo. In un env letto da Compose, il dollaro richiede l'escaping previsto dal renderer. Il renderer DR del repository gestisce questo caso.

Questo è lo **schema della nuova voce** da aggiungere alla lista `templates` della configurazione Agent; il percorso sorgente è un esempio da adattare alla tua installazione:

```yaml
templates:
  - source-path: /etc/infisical/templates/comet.env.tpl
    destination-path: /home/pi/streams-aio/.secrets/comet.env
    config:
      polling-interval: 60s
```

Il template deve esportare la cartella `/comet` e aggiungere l'alias condiviso della tabella, senza esportare tutte le chiavi `/shared` dentro ogni servizio. Usa la funzione di lettura compatibile con la versione installata: nei generatori di questo repo compare `secret`; la [documentazione Agent attuale](https://infisical.com/docs/integrations/platforms/infisical-agent) descrive `listSecrets`. Conserva la sintassi già verificata sulla VM.

Assegna alla macchina Agent l'accesso alla nuova cartella nell'ambiente corretto. Dopo aver modificato la configurazione locale dell'Agent:

```bash
sudo systemctl restart infisical-agent.service &&
systemctl is-active infisical-agent.service
```

Attendi la generazione del file e controlla solo presenza e permessi:

```bash
cd /home/pi/streams-aio
test -s .secrets/comet.env &&
stat -c '%a %U:%G %n' .secrets/comet.env
```

Il file deve essere leggibile dall'utente che esegue Compose e non accessibile a tutti. Mantieni proprietario e permessi coerenti con i file privati già funzionanti. Per modificare successivamente un valore basta la GUI Infisical; il mapping non deve essere ricreato ogni volta.

### 4. Preparare il database e il suo bootstrap DR

Predisponi database e ruolo Comet sul PostgreSQL locale. Mantieni la credenziale su Infisical e, se usi PgBouncer, aggiorna anche le sue associazioni private. Il Compose seguente presuppone che il database sia raggiungibile sulla rete scelta.

Per ricrearlo vuoto dopo un guasto serve anche un contratto di bootstrap. Per Comet è già dichiarato in `postgres_bootstrap.connections` nel [piano DR](../config/dr-render-plan.json):

```json
{
  "service": "comet",
  "ref": "/comet/DATABASE_URL"
}
```

Per un servizio nuovo equivalente vanno dichiarati anche gli input richiesti e verificata la compatibilità con il generatore SQL e la userlist PgBouncer. Non basta aggiungere una connessione al Compose. Questi controlli del bootstrap si aggiornano sul repository pubblico: il worker non sostituisce automaticamente il proprio piano con una copia locale.

**I comandi DR `init`, `render` e `prepare-state` servono su un host nuovo.** Non usarli per aggiungere un database al PostgreSQL già in esecuzione: l'inizializzazione PostgreSQL non riesegue gli script su un volume esistente. Segui [BOOTSTRAP.md](BOOTSTRAP.md) e [DISASTER-RECOVERY.md](DISASTER-RECOVERY.md) per il percorso di ripristino.

### 5. Dichiarare Comet nel Compose

Inserisci la voce sotto il `services:` esistente. Adatta porta, UID/GID e rete al tuo host:

```yaml
comet:
  image: g0ldyy/comet:latest
  restart: unless-stopped
  user: "1000:1000"
  env_file:
    - ./data/comet/.env
    - ./.secrets/comet.env
  volumes:
    - ./data/comet/data:/app/data
  ports:
    - "127.0.0.1:2020:2020"
  depends_on:
    postgres:
      condition: service_healthy
```

Il secondo env file prevale sul primo. Non inserire copie delle credenziali in `environment:`: quella sezione prevale su entrambi.

Questo schema usa la rete Compose ordinaria. Il Comet attuale usa integrazioni e rete diverse, incluso Gluetun: con `network_mode: service:gluetun` la pubblicazione della porta appartiene a Gluetun e il collegamento al database va adattato. Il bind `127.0.0.1` dell'esempio rende la porta disponibile sull'host; per usarla da un proxy in un altro container configura esplicitamente rete e raggiungibilità.

Riferimenti ufficiali: [opzioni Comet](https://github.com/g0ldyy/comet/blob/main/.env-sample), [deployment Comet](https://github.com/g0ldyy/comet/blob/main/deployment/docker-compose.yml) e [env file Compose](https://docs.docker.com/reference/compose-file/services/#env_file).

### 6. Validare e applicare in locale

```bash
cd /home/pi/streams-aio
test -s .secrets/comet.env &&
docker compose --profile all config --quiet &&
docker compose --profile all up -d comet &&
docker compose --profile all ps comet
```

`config --quiet` valida senza stampare i valori interpolati. L'avvio deve riuscire prima dell'esportazione: il worker usa le immagini presenti localmente e i loro digest pubblici per creare il lock di ripristino.

### 7. Controllare la pubblicazione e conservare il bootstrap privato

Il controllo seguente non pubblica:

```bash
SYNC_HOME=/home/pi/.local/share/streams-aio-public-sync
"$SYNC_HOME/venv/bin/python" -B "$SYNC_HOME/tooling/scripts/sync-public-stack.py" \
  --source /home/pi/streams-aio --seamless
```

Dopo `PUBLIC_SYNC_CHECK PASS`, attendi il timer oppure esegui una pubblicazione immediata e un backup cifrato aggiornato:

```bash
sudo systemctl start streams-aio-public-sync.service &&
sudo systemctl start streams-aio-dr.service &&
systemctl show streams-aio-public-sync.service streams-aio-dr.service \
  -p Id -p Result -p ExecMainStatus -p ExecMainExitTimestamp
```

Il risultato atteso è `Result=success` e `ExecMainStatus=0` per entrambi. Se non ci sono differenze pubbliche, non viene creato un nuovo commit. Il backup privato resta un processo distinto.

Su GitHub trovi dichiarazione del servizio, env pubblico, digest dell'immagine e directory vuote. Un target Agent nuovo è censito nel layout come input da recuperare tramite l'Agent: il nuovo backup cifrato è essenziale. Per integrare quel target anche nel renderer alternativo `dr.py`, aggiorna esplicitamente il piano e verifica le associazioni.

## Cambiare una preferenza pubblica

Esempio: aumentare `LIVE_TORRENT_CACHE_TTL` di Comet da `86400` a `172800`.

1. Modifica la riga in `data/comet/.env` sulla VM.
2. Valida e applica solo Comet:

   ```bash
   cd /home/pi/streams-aio
   docker compose --profile all config --quiet &&
   docker compose --profile all up -d --no-deps comet
   ```

3. Il timer esporta il valore pubblico modificato. Per anticiparlo usa `sudo systemctl start streams-aio-public-sync.service`.

Se l'impostazione è anche nel file privato o in `environment:`, prevale quella sorgente. Modifica il posto che effettivamente controlla il valore.

## Cambiare un secret esistente

Esempio: cambiare `CONFIGURE_PAGE_PASSWORD` di Comet.

1. Aggiorna il valore nella GUI Infisical, nella cartella e nell'ambiente già configurati.
2. Attendi il polling dell'Agent. La modifica del file non aggiorna l'ambiente di un container già avviato.
3. Applica il nuovo ambiente ricreando solo Comet:

   ```bash
   cd /home/pi/streams-aio
   test -s .secrets/comet.env &&
   docker compose --profile all config --quiet &&
   docker compose --profile all up -d --no-deps --force-recreate comet
   ```

4. Avvia `sudo systemctl start streams-aio-dr.service` per includere subito la nuova configurazione privata nel backup.

La ricreazione riusa i volumi; non li inizializza da zero. Il valore del secret non compare nel repository pubblico. Cambiare una credenziale di un database richiede anche la rotazione sul database, come indicato sopra.

## Aggiungere un servizio diverso

Applica lo stesso schema, usando il nome reale del nuovo servizio al posto di `nuovo-servizio`:

```text
data/nuovo-servizio/.env              preferenze pubbliche
data/nuovo-servizio/public/           eventuali file pubblici di testo
data/nuovo-servizio/data/             database, cache e runtime esclusi
.secrets/nuovo-servizio.env           secret distribuiti dall'Agent
```

Collega gli env file al Compose, aggiungi il mapping Agent se necessario e applica il servizio. Usa un'immagine pubblica con digest disponibile localmente. Il timer rileva il nuovo servizio e le directory montate, senza dover aggiungere ogni cartella a una lista manuale.

Per un file pubblico montato singolarmente usa un percorso ammesso, ad esempio `data/nuovo-servizio/public/settings.yaml`. Per un file privato usa una destinazione `.secrets/` o `.generated/` prodotta dall'Agent/bootstrap. Un nuovo file misto richiede un template e binding espliciti: **non copiarlo integralmente in `public/`**.

Nei template DR già dichiarati, un riferimento privato può avere questa forma:

```text
@@INFISICAL:/nuovo-servizio/PRIVATE_SETTING@@
```

Il riferimento è pubblico, il suo valore no. La sola presenza del marker non registra un nuovo file nel piano: dichiara formato, destinazione, binding e input del bootstrap. Le configurazioni gestite dall'applicazione si inizializzano una volta, senza sovrascrittura continua dell'Agent.

## Rimuovere un servizio

Prima ferma e rimuovi il solo container interessato, mentre la sua dichiarazione esiste ancora nel Compose. Esempio didattico Comet:

```bash
cd /home/pi/streams-aio
docker compose --profile all stop comet &&
docker compose --profile all rm -f comet
```

Poi rimuovi la dichiarazione Comet e le dipendenze che lo richiamano dal Compose, valida con `config --quiet` ed esegui il controllo pubblico. Il timer aggiorna l'inventario dei servizi attivi nel Compose. Questo esempio non cancella volumi o cartelle runtime.

Rimuovi un mapping Agent o una chiave Infisical solo dopo aver verificato che nessun altro servizio la usa. Le connessioni del bootstrap database e altri contratti espliciti richiedono il loro aggiornamento sul repo. Se la rimozione tocca binding privati, il worker può richiedere una revisione prima di pubblicare.

## Diagnosticare un blocco

Esegui il controllo senza pubblicazione:

```bash
SYNC_HOME=/home/pi/.local/share/streams-aio-public-sync
"$SYNC_HOME/venv/bin/python" -B "$SYNC_HOME/tooling/scripts/sync-public-stack.py" \
  --source /home/pi/streams-aio --seamless
```

Poi controlla solo esito e timer:

```bash
systemctl show streams-aio-public-sync.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp
systemctl list-timers streams-aio-public-sync.timer streams-aio-dr.timer --no-pager --full
```

| Errore o situazione | Verifica utile |
| --- | --- |
| Digest pubblico non disponibile | L'immagine è pubblica, presente localmente e associata al servizio corretto? |
| Credenziale letterale rifiutata | Sposta il valore in Infisical e usa la sorgente privata nel Compose/config |
| File bind privato senza template | Dichiara il suo bootstrap o rendilo un target privato dell'Agent |
| Rimozione di binding privati | Rivedi le associazioni del piano; non allargare gli ignore per eludere il controllo |
| Layout Honey cambiato | Verifica numero/posizioni delle righe e associazioni Infisical posizionali |
| Sorgente cambiata durante il controllo | Lascia stabilizzare le modifiche locali e ripeti |
| Servizio `inactive`, ultima esecuzione riuscita | Normale per un servizio `oneshot`; il timer deve essere attivo |

Conserva il codice di errore e i percorsi mostrati. Per chiedere assistenza bastano questi metadati: non pubblicare `.env` privati, contenuti di `.secrets`, output Compose interpolati o dump completi dei container.

## Cosa avere per un vero ripristino

| Da recuperare | Provenienza |
| --- | --- |
| Compose, env pubblici, template, manifest e script | Questo repository |
| Secret e impostazioni private | Infisical ripristinato dal suo backup cifrato |
| Agent, template privati e autenticazione di bootstrap | Backup privato Agent recente e credenziali esterne |
| Database Nginx Proxy Manager | Artifact cifrato NPM nella release DR |
| Chiave age privata e accessi GitHub/host | Custodia esterna recuperabile senza la VM persa |
| Payload logici AIO richiesti | Percorso esistente descritto nella guida AIO |

I database applicativi ricostruibili e le cache possono ripartire vuoti. Un nuovo servizio con preferenze presenti soltanto nel suo database richiede una scelta esplicita: ricrearle manualmente oppure aggiungere un export dichiarativo/backup dedicato. La sincronizzazione non estrae automaticamente ogni impostazione dalle GUI delle applicazioni.

Continua con [BOOTSTRAP.md](BOOTSTRAP.md), [DISASTER-RECOVERY.md](DISASTER-RECOVERY.md), [NPM-RECOVERY.md](NPM-RECOVERY.md) e [AIO-EXPORT-IMPORT.md](AIO-EXPORT-IMPORT.md). Gli esempi operativi non sostituiscono una prova di ripristino su un host nuovo.
