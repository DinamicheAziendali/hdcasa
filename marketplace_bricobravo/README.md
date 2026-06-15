# Marketplace - BricoBravo

Connettore ordini BricoBravo (Famiglia A), sopra `integrations_core`.

## Cosa fa (ossatura)
- `BricoBravoConnector(MarketplaceConnector)` usa `RestTransport` per l'API
  BricoBravo (`https://sellerhub.bricobravo.com/api`, auth header `sh-token`).
- **pull_orders()** — GET `/orders` con paginazione e filtri (acquired=0 di
  default; date `YYYYMMDDHHIISS`).
- **import_order()** — mappa l'ordine in un vero `sale.order` Odoo:
  prodotti per `barcode=ean` → fallback `default_code=product_code` → fallback
  `centrivo.sku.map`. **Se un prodotto non esiste in Odoo, l'ordine va in
  errore** (Odoo è la fonte di verità, non si creano prodotti). Dati fiscali da
  `invoice_info`, indirizzo da `shipping_info`.
- **mark_acquired()** — PATCH `/orders/{id}/acquired`.
- **push_shipment()** — PATCH `/orders/{id}/shipped` (corriere + tracking,
  multi-collo via virgola; punto di aggancio per ShipTracker/carrier_*).

## Ordine delle operazioni (idempotenza)
1. controllo `centrivo.order.map` (channel + external_id);
2. crea `sale.order` in Odoo;
3. registra `centrivo.order.map` (imported);
4. **solo dopo il successo** chiama `mark_acquired`.

## Stati ordine BricoBravo
`0 paid`, `1 completed`, `2 to_refund`, `3 refunded` → mappati in
`BRICOBRAVO_STATUS_MAP`.

## Configurazione canale
Creare un record **Integrations → Canali**:
- Connettore = BricoBravo
- Ambiente = Sandbox (la sandbox resetta gli ordini ogni ora) / Produzione
- Base URL = vuoto (usa il default) o l'URL fornito
- API Key = **la chiave (header `sh-token`) — fornita separatamente, NON
  versionata nel repo**

## Da fare (task successivi)
- Rifinire le chiamate reali (`RestTransport.request`, retry 5xx) sulla sandbox.
- v2: export **giacenze** via CSV (CsvTransport) — EXPORT da Odoo.
- v3: export **catalogo** via CSV (CsvTransport) — EXPORT da Odoo.
  (Colonne dei feed indicate come commento nel `__manifest__.py`.)
