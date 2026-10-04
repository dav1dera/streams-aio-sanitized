# Bootstrap · checklist per un host nuovo

[← README](../README.md) · **[Procedura unica di ripristino](DISASTER-RECOVERY.md)**

Questa pagina raccoglie i prerequisiti. Per l'ordine dei comandi segui la guida unica: non usare questo elenco come un secondo installer e non eseguire gli strumenti fresh sullo stack già attivo.

## Tre fonti, con compiti diversi

- **Stack locale:** Compose, impostazioni pubbliche e file dichiarativi attivi. Il timer sulla VM li esporta verso GitHub circa ogni 15 minuti.
- **Infisical:** secret e impostazioni private; l'Agent nativo li distribuisce nei file locali. Cartella GUI, mapping Agent e riferimento Compose sono tre parti dello stesso collegamento.
- **Release DR privata:** cinque artifact age di NPM, Agent e Infisical, più manifest/checksum. Massimo cinque release gestite; nessuna private age key nel repo.

Il clone pubblico da solo non ricrea autenticazione, secret, Infisical o il database NPM. La clonazione GitHub si usa per **recuperare** un host nuovo; durante l'uso quotidiano la configurazione segue locale → GitHub.

## Prima di iniziare

| Prerequisito | Verifica |
| --- | --- |
| Accesso GitHub recuperabile senza Infisical perso | Accesso al repo pubblico e privato |
| Identity age esterna | Una release può essere decifrata in storage privato |
| Infisical recuperato | Progetto/ambiente e autenticazione funzionanti |
| Agent e template privati recenti | Mapping e percorsi corrispondono al commit pubblico scelto |
| Host compatibile | Python 3.11+, Docker/Compose, Git, gh, venv, ACL, CLI Infisical |
| Versioni e architettura delle immagini | Digest disponibili e contratti NPM/AIO compatibili |
| NPM SQLite coerente | Artifact della release verificato, NPM ancora spento |
| Payload AIO dichiarati | Export esterni e binding Infisical per il preflight completo attuale |
| Bootstrap infrastrutturale | Reti, DNS, UID/GID e accessi coerenti con il nuovo host |

## Configurazioni generate una volta

`dr.py init` marca soltanto una destinazione vuota. `render --mode fresh` crea env privati, configurazioni e SQL di bootstrap dopo aver verificato gli input; `prepare-state` crea directory vuote con permessi/ownership dichiarati. Non avviano container e non sovrascrivono configurazioni esistenti.

Honey, Headscale/Headplane, Jackett e Seanime possono riscrivere la propria configurazione: inizializzali una volta. Non impostare l'Agent per sovrascriverli continuamente. Un target privato nuovo deve essere collegato esplicitamente all'Agent; un database nuovo sul PostgreSQL condiviso deve avere anche il suo bootstrap dichiarato.

L'ordine degli env è pubblico prima, privato dopo. `environment:` prevale su entrambi. Cambiare un secret distribuito non aggiorna da solo l'ambiente del container già avviato, salvo un hook di ricreazione esplicitamente configurato.

## Cosa si ricrea vuoto

Database applicativi dichiarati come ricostruibili, cache, sessioni e runtime possono ripartire vuoti. Infisical e NPM hanno gli artifact necessari per il modello scelto. Gli AIO mantengono il percorso logico esistente; il preflight completo richiede i suoi input separati. Media personali e configurazioni nuove salvate solo nel DB richiedono una scelta di backup propria.

Concludi con validazione Compose, avvio per dipendenze, NPM/TLS, accessi/enrollment e verifica dei timer come nella [guida unica](DISASTER-RECOVERY.md). Una validazione sintetica non dimostra il funzionamento di un host ricostruito con credenziali reali.
