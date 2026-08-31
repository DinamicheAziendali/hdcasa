# Marketplace - Temu

Connettore Temu per Odoo 18 (Famiglia A, sotto `integrations_core`).

⚠️ **Non è più un modulo in sola lettura.** La descrizione «Fase 1 parte A:
ricognizione in sola lettura, nessuna scrittura verso Temu» è rimasta qui e nel
manifesto per mesi dopo che il codice aveva smesso di essere vera: oggi il
modulo scrive su un catalogo pubblico e su ordini veri.

## ⛔ I due blocchi che oggi fermano tutto, e non dipendono dal codice

Vanno letti **prima** dell'elenco qui sotto: il modulo è scritto, ma con la
situazione di oggi due cose lo fermano, e nessuna delle due si risolve
scrivendo codice.

1. **L'indirizzo IP di Odoo.sh non è mai stato dichiarato a Temu.** Temu
   accetta chiamate **solo** dagli IP dichiarati, e oggi ce n'è uno solo:
   `167.233.111.64`, il server Hetzner. Il modulo è destinato a Odoo.sh, e **da
   lì non passa nemmeno una chiamata** — permesso o non permesso. L'IP si
   aggiunge dal Centro Partner («Adjust server IP»), ma prima va accertata una
   cosa che nessuno sa ancora: **Odoo.sh espone un indirizzo di uscita stabile
   e unico?**
2. **Il permesso sugli importi ordine è negato (`3000032`), e ferma OGNI
   import di ordini.** L'interfaccia degli importi non è mai stata dichiarata
   alla registrazione dell'app: senza di essa il modulo non può leggere quanto
   ha pagato il cliente, e senza quello **non entra un solo ordine**. Non si
   risolve rifacendo l'autorizzazione (provato e misurato: 146 permessi
   identici) né dal Centro Partner, che cambia solo IP, negozi e Paesi.
   **Il ticket è già aperto** presso l'assistenza Temu e si segue dal **Seller
   Center → Assistenza**; deve essere **Temu a modificare la registrazione**.
   E le interfacce mancanti sono **otto**: vanno chieste tutte insieme, perché
   la domanda si fa una volta sola.

Il bottone «Verifica token» sulla scheda del canale dice, in ogni momento,
quali chiamate risultano scoperte. Dettaglio e prove:
`docs/temu-cosa-gli-manca.md`.

## Cosa fa

⚠️ Al netto dei due blocchi qui sopra: questo è ciò che il codice fa, non ciò
che oggi riesce a fare in produzione.

- **Aggancia le schede che Temu ha già** ai prodotti Odoo, per riferimento
  interno (SKU). La ricognizione del catalogo resta in sola lettura.
- **Tiene allineati prezzo e giacenza.** Il prezzo non cambia subito: Temu apre
  una pratica di revisione, e il connettore sa richiederne l'esito.
- **Scarica gli ordini** e li traduce in ordini Odoo (spese di spedizione
  comprese, se l'articolo è configurato). ⚠️ **Oggi, con questo token, non ne
  entra nemmeno uno**: si veda il blocco 2 qui sopra.
- **Comunica le spedizioni** a Temu: corriere e numero di tracking, e sa
  rileggere da Temu lo stato di una spedizione già comunicata.
- **Invia le fatture** e le note di credito.

## Cosa NON fa, ed è una scelta

- **Non pubblica schede nuove.** Le schede le crea l'interfaccia Temu da file:
  questo modulo le trova già lì e le aggancia.
- **Non corregge le schede esistenti** — né titoli, né descrizioni, né
  attributi.
- **Non carica immagini.**

La pubblicazione è **fuori perimetro** per decisione presa il 2026-08-27. Fino a
quel giorno quattro campi della schermata del canale (regione, giorni di
evasione, giorni dichiarati, template di spedizione) ne erano l'impronta:
chiesti all'utente e **letti da nessuna riga di codice**. Sono stati tolti. Se
la pubblicazione rientrerà nel perimetro, torneranno insieme al codice che li
usa.

## Prima di accenderlo

- **Tutti i cron nascono spenti.** Si accendono a mano, uno per volta, dopo aver
  verificato il contenuto degli invii.
- **La simulazione (`temu_simulate`) è la sicura**: finché è accesa, le
  scritture verso Temu vengono preparate e registrate ma non inviate.
- Il modulo **non è mai stato installato da nessuna parte**: la prima
  installazione è ancora tutta da fare, ed è la sola prova che vale.
- ⚠️ E prima ancora: **i due blocchi in cima a questa pagina**. Accendere i
  cron degli ordini prima che il permesso importi sia concesso significa
  riempire il registro di errori identici ogni sei ore.

Specifica: `docs/Progetto_marketplace_temu.md` e
`docs/Progetto_marketplace_temu_ciclo_completo.md`.
Piano: `docs/Piano_marketplace_temu_fase1a.md`.
