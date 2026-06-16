# Integrations Core

Layer condiviso (Famiglia A) per i connettori marketplace "semplici"
(BricoBravo, ManoMano). Fornisce:

- **Contratti** (`connectors/base.py`): classe astratta `MarketplaceConnector`
  con `pull_orders()`, `import_order()`, `mark_acquired()`, `push_shipment()`,
  e un registro dei connettori (`@register_connector`).
- **Trasporti** (`connectors/transport.py`): `TransportBase`, `RestTransport`
  (API REST/JWT, con predisposizione retry sui 5xx), `CsvTransport` (feed
  CSV/FTP, per export v2/v3). Il trasporto è separato dal connettore.
- **Registri persistiti** (modelli Odoo):
  - `centrivo.channel` — configurazione di un marketplace (credenziali,
    ambiente sandbox/produzione, company).
  - `centrivo.order.map` — idempotenza: ordine esterno ↔ sale.order.
  - `centrivo.sku.map` — mapping SKU di fallback (Odoo è fonte di verità).
  - `centrivo.job.log` — log operativo.
- **Infrastruttura job**: `ir.cron` (disattivo di default) che chiama
  `cron_pull_all_channels()` con isolamento e logging per canale.

## Principio architetturale
**Odoo è la fonte di verità dei prodotti.** I prodotti esistono già in Odoo: i
connettori NON li creano dagli ordini. Lo stock multi-canale si unifica sui
modelli nativi Odoo (veri `sale.order`, veri movimenti di stock), non in un
meta-layer.

## Sicurezza
Nessuna credenziale vive nel codice: `api_key` è un campo del record
`centrivo.channel`, valorizzato in ambiente.
