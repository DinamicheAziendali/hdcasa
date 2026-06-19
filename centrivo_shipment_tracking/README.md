# Centrivo Shipment Tracking (BASE)

Modulo base della suite di **tracking post-spedizione**. Fornisce:

- **Modello a due livelli**: `centrivo.shipment` (spedizione, fonte di verità) e
  `centrivo.shipment.parcel` (collo), per gestire multicollo e consegna parziale.
- **Eventi** `centrivo.shipment.event` (checkpoint storici, append-only).
- **Normalizzazione stati via tabella** `centrivo.shipment.status.map`
  (`(corriere, codice grezzo) → stato Centrivo`), con fallback `sconosciuto` + log.
- **Stati** `centrivo.shipment.status` (sistema protetti + utente ampliabili).
- **Stato reale sul picking** (specchio, non tocca il nativo `state`).
- **Polling adattivo**: bottone manuale "Aggiorna tracking ora" (singolo + massivo)
  e cron `cron_poll_active_shipments` (nasce DISATTIVO).
- **Contratto astratto `TrackingConnector`** + registro: gli adattatori concreti
  (GLS/BRT/Poste) sono moduli separati.

**Suite AUTOSUFFICIENTE**: il trasporto HTTP è interno
(`connectors/transport.py` → `RestTransport`) e il log è interno
(`centrivo.shipment.log`). Dipende SOLO da moduli standard di Odoo
(`stock`, `stock_delivery`, `sale_stock`, `mail`): **non** dipende da
`integrations_core` né da `centrivo_carrier_base`. Vedi
`Progetto_tracking_corrieri.md` (Fase 1).

## Registrazione al license server (privacy/trasparenza)

All'**installazione** e a ogni **aggiornamento di versione**, questo modulo invia un
*ping di registrazione* best-effort al **Centrivo License Server**
(`https://license.centrivo.app/register`, configurabile da Impostazioni → Tecnico →
Parametri di sistema, chiave `centrivo.license.server_url`).

Vengono inviati i **dati minimi** necessari a sapere su quali database è installata la
suite tracking:

- `dbuuid` — identificativo univoco del database (`database.uuid`);
- `product` — costante `tracking`;
- `module_version` — versione del modulo dal manifest;
- `instance_url` — URL dell'istanza (`web.base.url`);
- `contact` — riferimento **opzionale** impostato dall'amministratore (vuoto se non
  configurato), in *Spedizioni → Configurazione → Impostazioni tracking*.

**Nessun dato di spedizione, ordine o cliente lascia l'istanza.** La registrazione è
*best-effort*: timeout corto (5 s), errori silenziosi e **nessun blocco** di
funzionalità se il server non risponde (FASE 1: nessun gating, nessuna verifica
licenza). Il pulsante **«Registra ora»** nelle impostazioni consente il re-invio
manuale.
