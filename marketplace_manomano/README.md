# Marketplace - ManoMano

Connettore offerte ManoMano (Famiglia A), sopra `integrations_core`.

## Cosa fa (ossatura)

- `ManoManoConnector(MarketplaceConnector)` usa `RestTransport` per l'API
  ManoMano Partners (`https://partnersapi.sandbox.manomano.com` sandbox,
  `https://partnersapi.manomano.com` produzione). Auth reale: header
  **`x-api-key: <API_KEY>`** (letto da `channel.api_key`, MAI nel codice) +
  **`x-thirdparty-name: <NomeAzienda>_<Versione>`** (dal campo canale
  `manomano_thirdparty_name`, es. `HDcasa_1.0`).
- **push_offers()** — **CREA e aggiorna** (upsert) le offerte ManoMano via
  `PUT /api/v1/offers?seller_contract_id=<id>` (contract in query string,
  body = ARRAY di offerte per SKU).
  - Agganciamento ai prodotti ManoMano: l'offerta è **per SKU**
    (`default_code`). **L'EAN non entra nel payload dell'offerta**: serve solo
    per la verifica separata a catalogo (`POST
    product-catalog/seller-catalog/api/v1/products/ean/search`), utile per
    sapere se un EAN è già a catalogo ManoMano e pubblicabile.
  - Odoo è la **fonte di verità** (sola lettura sui prodotti): invia 
    variazioni di prezzo/stock dai listini di vendita di Odoo e crea offerte
    nuove per i prodotti non ancora presenti su ManoMano.
  - SKU inviato = **Riferimento interno (`default_code`)** del prodotto.
  - **Prezzo inviato**: il **listino di vendita** del canale
    (`pricelist_selling_id`, `pricing.price_vat_included`) — si vende a quel
    prezzo. Il prezzo barrato/scontato (`pricing.retail_price_vat_included`,
    che su ManoMano deve essere PIÙ ALTO del prezzo pagato) **non viene
    inviato per ora**: è un affinamento futuro, da abilitare solo dopo aver
    confermato con Angelo la semantica dello sconto.
  - **Tempo di lavorazione**: segue il Customer Lead Time del prodotto 
    (`sale_delay`), oppure il default del canale se il prodotto ha lead time 0.
  - **Contract**: `seller_contract_id`, un **ID numerico** (es. `11781522`),
    NON un codice mercato "IT"/"FR". Multi-contract: più ID separati da
    virgola nel tab ManoMano del canale; il connettore itera su ognuno.
  - Precondizioni per ogni riga: prodotto in Odoo con `default_code` + prezzo
    di vendita nel listino + quantità disponibile (secondo il tipo/ambito
    configurato) + griglia di spedizione (`manomano_carrier_grid_name`) sul
    canale. Se una riga non le soddisfa, viene saltata con segnalazione nel
    log.

## Configurazione canale

Creare un record **Integrations → Canali**:
- **Connettore** = ManoMano
- **Ambiente** = Sandbox (la sandbox resetta le offerte ogni ora) / Produzione
- **Base URL** = vuoto (usa il default secondo l'ambiente) o l'URL fornito
- **API Key** = **chiave di autenticazione (header `x-api-key`) — fornita
  separatamente, NON versionata nel repo**

### Tab ManoMano
- **Contract** = ID numerici del contratto seller ManoMano
  (`seller_contract_id`), separati da virgola se più di uno. Esempi:
  - `11781522` (un solo contract/mercato)
  - `11781522,124578` (più contract)

  Il connector itererà su ogni contract quando sincronizza. **Non** un codice
  mercato tipo "IT"/"FR": ManoMano identifica il seller per contratto tramite
  un ID numerico.
- **Thirdparty name** = valore dell'header `x-thirdparty-name` richiesto dalle
  chiamate ordini (es. `HDcasa_1.0`).
- **Griglia di spedizione** = nome della griglia configurata nel back-office
  ManoMano, usata nella creazione offerta (`shipping.carrier_grid[].name`).

### Tab Export
- **Listino di vendita** = quale listino Odoo usare per i prezzi
- **Tipo quantità** / **Ambito quantità** = come calcolare la giacenza
  disponibile (quantità prenotata, quantità virtuale, ecc.)
- **Tag prodotti** (facoltativo) = restringere la sincronizzazione ai soli
  prodotti con questi tag. Se vuoto, tutti i prodotti con default_code e
  prezzo vengono sincronizzati.

## Come si lancia

- **Manuale**: Tab ManoMano del canale → bottone **"Sincronizza offerte"**.
  Crea le offerte mancanti e aggiorna quelle esistenti (upsert per SKU).
  Invia immediatamente il batch e registra l'operazione nel log.
- **Automatico**: Cron **"ManoMano: sincronizza offerte (crea e aggiorna)"** è presente ma
  **SPENTO di default**. Si attiva dopo la validazione sulla sandbox.

## Dove si leggono gli esiti

Log operazioni (`Integrations → Log operazioni`):
- Cerca **Operazione** = `push_offers`
- Ogni riga di log contiene:
  - Canale e contract sincronizzato
  - Numero righe valide (pronte per l'invio)
  - Numero righe inviate con successo
  - Numero righe saltate (prodotti senza default_code, senza prezzo, ecc.)
  - Blocchi in errore (errori di rete, risposta negativa ManoMano)
  - Eventuale messaggio di errore per diagnosticare fallimenti

## Ciclo ordini

Il connettore copre l'intero ciclo di un ordine ManoMano, dal download fino
alla comunicazione della spedizione:

1. **Scarico ordini (`pull_orders`)** — per ogni contract configurato,
   scarica gli ordini via API (paginati) e li importa uno per uno. Si lancia
   dal bottone "Scarica ordini" del canale, oppure automaticamente dal cron
   generico di `integrations_core` (vedi sotto). Prima di scaricare nuovi
   ordini, ritenta l'accettazione degli ordini rimasti "pendenti"
   (`acquired_done=False`).
2. **Creazione `sale.order` (`import_order`)** — traduce l'ordine ManoMano in
   un ordine di vendita Odoo. **Odoo è la fonte di verità**: i prodotti non
   vengono creati al volo. Se anche una sola riga non trova corrispondenza
   (via `centrivo.sku.map`, EAN o riferimento interno), **l'intero ordine va
   in errore** (`centrivo.order.map` stato `error`) — nessun ordine parziale.
   L'operazione è idempotente: un ordine già importato viene saltato.
3. **Accettazione automatica (`accept_order`)** — subito dopo l'import
   riuscito, il connettore accetta l'ordine su ManoMano in automatico
   (nessun intervento manuale). Un fallimento di rete/HTTP non blocca
   l'import: l'ordine resta `acquired_done=False` e viene ritentato al
   prossimo `pull_orders`.
4. **Spedizione (`push_shipment`)** — comunica a ManoMano il corriere e il
   tracking. Precondizioni:
   - l'ordine è importato (`centrivo.order.map` stato `imported`) con un
     `sale.order` collegato;
   - esiste **esattamente un** `stock.picking` in stato `done` con
     `carrier_tracking_ref` valorizzato (campo nativo Odoo) — **il
     multi-collo non è supportato** (più picking pronti → errore, nessun
     invio parziale);
   - il corriere del picking è mappato in `centrivo.carrier.map` per il
     canale (campo `external_code`);
   - il **tracking URL è OBBLIGATORIO**: il `tracking_url_template` del
     mapping corriere deve contenere il segnaposto `{tracking}` — senza,
     ManoMano rifiuta lo shipment e il push si ferma in errore (nessun invio
     senza URL).
   Operazione idempotente su `shipment_pushed`: un ordine già spedito viene
   saltato; in caso di errore resta ritentabile.

**Pull e push usano i cron generici di `integrations_core`** (lo stesso
meccanismo di BricoBravo): non ci sono cron dedicati a ManoMano per gli
ordini. Le cron sono **presenti ma SPENTE di default**: Angelo le attiva
manualmente dopo la validazione in sandbox.

### Punti ancora aperti

Endpoint, header, contract, offerte e ciclo ordini (accept/ship) sono ormai
sugli endpoint REALI dell'API (fonte di verità: `docs/manomano-api-reference.md`,
costruita dagli esempi curl ufficiali ManoMano). Restano aperti solo:

1. **Comportamento `eco_participation` per il mercato IT**: il campo è
   obbligatorio su FR (l'API segnala `INVALID_ECO_PARTICIPATION` se assente);
   da chiarire se/come si applica anche su IT.
2. **Accept/spedizione multi-contract**: `accept_order` e `push_shipment`
   assumono attualmente **un solo contract per canale** (usano il primo
   configurato, `_first_contract_id`). Corretto nel caso attuale (IT, un
   contract); con più contract configurati un ordine del secondo mercato
   verrebbe accettato/spedito col contract sbagliato. Affinamento futuro:
   memorizzare il `seller_contract_id` dell'ordine e usarlo in accept/ship
   (vedi `docs/manomano-api-reference.md`).

## Feed prodotto (Strato 3)

Il feed prodotto è il **contenuto (scheda)** che ManoMano usa per creare o
approvare gli articoli a catalogo: è il **prerequisito** delle offerte
(`push_offers`) — finché un articolo non è a catalogo e approvato, le sue
offerte tornano in errore `NO_CONTENT_APPROVED`.

- **Mappatura configurabile**: nel tab **Feed prodotto** del canale, la
  griglia mappa ogni colonna del feed ManoMano (scelta tra i **campi
  scaricati dalla Taxonomy** col bottone «Aggiorna campi da ManoMano») a un
  **campo Odoo**, a un **valore fisso**, o a un'**immagine di galleria**
  (indice). Colonne obbligatorie: `sku`, `ean` (o `sku_manufacturer`),
  `brand`, `title`, `description`, `image_1`.
- **Generazione**: bottone **"Genera feed prodotto"** nel tab → crea/aggiorna
  il CSV (intestazione dai campi scaricati dalla Taxonomy, riserva cablata se
  non ancora scaricata) e valorizza `manomano_product_feed_url` +
  `manomano_product_feed_generated_at`.
- **Come usarlo**:
  - **Automatico (consigliato)**: incolla l'URL del feed (col token, copiabile
    con l'icona) nella **Toolbox ManoMano → Catalogo → importazione
    automatica**. ManoMano lo scarica periodicamente da solo.
  - **Manuale**: scarica il CSV dall'URL e caricalo a mano nella stessa
    sezione della Toolbox.
- **Cron**: **"ManoMano: genera feed prodotto"** è presente ma **SPENTO di
  default** (ogni 12 ore se attivato). Si attiva dopo la validazione sulla
  sandbox, come gli altri cron del modulo.

## Roadmap futura

- **Strato 2** (creazione offerte nuove): **COMPLETATO**. Il connettore
  crea offerte nuove e aggiorna quelle esistenti (upsert per SKU).
- **Gestione ordini**: **COMPLETATO**. Ciclo completo scarico → creazione
  `sale.order` → accettazione automatica → spedizione con tracking URL
  obbligatorio, sugli endpoint batch reali (vedi sezione "Ciclo ordini"
  sopra).
- **Strato 3** (schede prodotto): **COMPLETATO**. Mappatura colonne
  configurabile, generazione feed (intestazione dalla Taxonomy scaricata) e
  rotta pubblica protetta da token per l'importazione automatica su ManoMano
  (vedi sezione "Feed prodotto (Strato 3)" sopra).

Riferimento API: `docs/manomano-api-reference.md`. Riferimento progetto
completo: `docs/Progetto_marketplace_manomano.md`.
