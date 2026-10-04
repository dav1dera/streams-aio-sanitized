# Ambito attuale e verifiche

Documento aggiornato al sistema finalizzato; sostituisce il vecchio perimetro dell'audit iniziale.

- La configurazione dello stack segue **locale → GitHub**, attraverso l'esportatore seamless ogni circa 15 minuti.
- Env pubblici, Compose, file di testo ammessi, template dichiarati e directory montate vengono esportati. Secret, runtime, database, chiavi e file sconosciuti nei dati restano esclusi.
- Infisical contiene i valori privati; i mapping Agent locali restano essenziali e sono conservati nel backup cifrato.
- Il processo privato conserva NPM, Agent e Infisical in GitHub Releases age, con SHA256, controlli fail-closed, retention 5 e cleanup dopo successo verificato.
- AIOStreams/AIOMetadata mantengono il percorso logico esistente. Gli export non vengono aggiunti automaticamente alle release NPM/Agent/Infisical.
- Policy, CI, unità e tooling dell'esportatore sono gestiti dal repo; il worker non applica Compose da GitHub alla produzione.

La sincronizzazione e il backup sono stati eseguiti realmente. I test di preparazione usano input sintetici. **Non è stata completata una prova di ripristino integrale su host nuovi.** Lo stato dettagliato e la procedura unica sono in [DISASTER-RECOVERY.md](docs/DISASTER-RECOVERY.md).

La copia locale delle guide si aggiorna esplicitamente con il helper del [README](README.md#tenere-le-guide-locali-allineate). Questo aggiornamento copia solo documentazione e immagini; non è una sincronizzazione automatica GitHub → deployment.
