# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore BricoBravo — ossatura ordini via API REST.

API reale BricoBravo (Seller Hub):
  - Base URL : https://sellerhub.bricobravo.com/api
  - Auth     : header "sh-token: <API_KEY>"  (chiave fornita separatamente,
               MAI nel codice: si legge da channel.api_key)
  - Ordini   : GET /orders  (paginazione page/per_page; filtri start_datetime,
               end_datetime nel formato YYYYMMDDHHIISS, acquired, all)
  - Acquired : PATCH /orders/{id}/acquired  body {"acquired": 1}
  - Spedito  : PATCH /orders/{id}/shipped   body {courier, tracking_number,
               tracking_url}

Stato di questo file (FASE B):
  - Strato 1 (TASK_12): chiamata HTTP reale + pull "grezzo" con logging.
  - Strato 2 (TASK_13): import_order crea un vero sale.order in BOZZA, con
    cascata di risoluzione prodotto a 4 livelli, dati fiscali non bloccanti e
    pulizia dell'anagrafica cliente.
  - Strato 3a (TASK_16): dopo OGNI import riuscito (sale.order creato +
    order.map=imported) si chiama mark_acquired (PATCH /orders/{id}/acquired)
    in modo AUTOMATICO. L'esito è tracciato dal campo acquired_done su
    centrivo.order.map; gli acquired falliti vengono ritentati al pull
    successivo (retry_pending_acquired). push_shipment resta allo strato 3b.

Principio: Odoo è la fonte di verità dei prodotti. Se un prodotto dell'ordine
non esiste in Odoo, l'ordine NON viene creato: si registra un errore.
"""
import json
import logging
import re

from odoo import fields

from odoo.addons.integrations_core.connectors.base import (
    MarketplaceConnector,
    register_connector,
)
from odoo.addons.integrations_core.connectors.transport import (
    CsvSerializer,
    RestTransport,
    TransportError,
)

_logger = logging.getLogger(__name__)

# Limite di sicurezza pagine per lo STRATO 1 (evita loop infiniti in test).
MAX_PULL_PAGES = 10
# Numero di caratteri del payload diagnostico salvato nel log.
PAYLOAD_TRUNCATE = 2000


def _truncate(text, limit=PAYLOAD_TRUNCATE):
    """Tronca un testo per il logging diagnostico (evita log enormi)."""
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else (text[:limit] + "…[troncato]")


def _clean(text):
    """Normalizza una stringa anagrafica: collassa spazi multipli e fa strip.

    I dati BricoBravo sono "sporchi" (spazi finali, doppi spazi, ragioni sociali
    infilate nei nomi). Qui ci limitiamo a ripulire gli spazi; la separazione
    persona/azienda è un affinamento futuro.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()

# URL di default dell'API BricoBravo (sovrascrivibile da channel.base_url).
BRICOBRAVO_DEFAULT_BASE_URL = "https://sellerhub.bricobravo.com/api"

# Intestazioni ESATTE del feed prezzi/giacenze BricoBravo (ordine e nomi
# tassativi, dal sample ufficiale). Separatore CSV ';'.
STOCK_FEED_HEADERS = [
    "Sku EAN/GTIN",
    "Selling Price (Price to GPP)",
    "Discounted Price",
    "Available Quantity",
    "Processing Time",
    "Product_Code",
]

# Intestazioni ESATTE del feed CATALOGO completo BricoBravo (23 colonne, ordine
# tassativo dal sample ufficiale). Separatore CSV ';'.
CATALOG_FEED_HEADERS = [
    "Sku EAN/GTIN",
    "Category",
    "Brand",
    "ProductName",
    "Url",
    "Weight",
    "Product Description",
    "Image URL 1",
    "Image URL 2",
    "Image URL 3",
    "Image URL 4",
    "Image URL 5",
    "Image URL 6",
    "Image URL 7",
    "Image URL 8",
    "Image URL 9",
    "Image URL 10",
    "Selling Price (Price to GPP)",
    "Discounted Price",
    "Vat",
    "Available Quantity",
    "Processing Time",
    "Product_Code",
]

# Mappatura degli stati ordine BricoBravo verso uno stato interno leggibile.
# Valori BricoBravo: 0 paid, 1 completed, 2 to_refund, 3 refunded.
BRICOBRAVO_STATUS_MAP = {
    0: "paid",       # pagato — da evadere
    1: "completed",  # completato
    2: "to_refund",  # da rimborsare
    3: "refunded",   # rimborsato
}

# Lista CHIUSA dei corrieri ammessi da BricoBravo (codice → etichetta), da doc
# ufficiale. È la lista che il connettore espone (carrier_codes) al modello
# centrivo.carrier.map per il Selection dinamico. Il codice corretto per ogni
# spedizione viene scelto a mano nel carrier.map (o, in futuro, dai carrier_*).
BRICOBRAVO_CARRIERS = [
    ("dhl", "DHL Express"),
    ("gls-italy", "GLS Italy"),
    ("italy-sda", "Poste Italiane (SDA)"),
    ("tnt-it", "TNT"),
    ("brt-it", "BRT"),
    ("ups", "UPS"),
    ("fedex", "FedEx"),
    ("poste-italiane", "Poste Italiane"),
    ("dpd", "DPD"),
    ("liccardi-express", "Liccardi Express"),
    ("kuehne-nagel", "Kuehne Nagel"),
    ("fercam", "Fercam"),
    ("dachser", "DACHSER"),
    ("raben-group", "Raben Group"),
    ("dbschenker-se", "DB Schenker"),
    ("other", "Altro"),
]


@register_connector("bricobravo", "BricoBravo")
class BricoBravoConnector(MarketplaceConnector):
    """Connettore concreto per BricoBravo (Famiglia A)."""

    # URL di default (uguale per sandbox e produzione): precompila base_url sul
    # channel quando si sceglie BricoBravo. Vedi onchange su centrivo.channel.
    default_base_url = BRICOBRAVO_DEFAULT_BASE_URL

    # Lista chiusa dei corrieri ammessi da BricoBravo (vedi BRICOBRAVO_CARRIERS).
    # Esposta alla base (carrier_codes) per il Selection dinamico del carrier.map.
    carrier_codes = BRICOBRAVO_CARRIERS

    def __init__(self, channel):
        super().__init__(channel)
        # Trasporto REST con il token di autenticazione nell'header.
        # La chiave viene dal channel (campo api_key), valorizzato in ambiente:
        # nel codice resta solo il NOME dell'header, mai il valore.
        base_url = channel.base_url or BRICOBRAVO_DEFAULT_BASE_URL
        self.transport = RestTransport(
            base_url=base_url,
            default_headers={"sh-token": channel.api_key or "__API_KEY_DAL_CHANNEL__"},
        )

    # ==================================================================
    # 1) PULL ORDINI
    # ==================================================================
    def pull_orders(self, start_datetime=None, end_datetime=None, acquired=0):
        """Pull ordini: scarica, logga e (STRATO 2) IMPORTA ogni ordine.

        GET /orders con header sh-token, paginazione (per_page=100, acquired=0 di
        default), limite di sicurezza di MAX_PULL_PAGES pagine.

        Per ogni pagina:
          - logga il payload del primo ordine (diagnostica, api_key mai loggata);
          - per OGNI ordine ricevuto chiama self.import_order(...) che crea il
            sale.order in modo idempotente.

        Esiti scritti in centrivo.job.log:
          - successo : "Ricevuti N ordini (importati I, errori E), pagine X".
          - nessun ordine: "Nessun ordine da scaricare".
          - errore BricoBravo (chiave "errors" o HTTP non 2xx): result=error.
          - errore di rete: result=error con il messaggio dell'eccezione.
        Aggiorna last_pull sul channel.
        """
        channel = self.channel
        company = channel.company_id
        JobLog = self.env["centrivo.job.log"]

        # STRATO 3a — RITENTA gli acquired pendenti PRIMA di scaricare nuovi
        # ordini: ordini già importati (state=imported) ma non ancora marcati
        # acquisiti (acquired_done=False) per un fallimento precedente di rete/HTTP.
        # È il meccanismo di recupero semplice descritto nel report.
        self.retry_pending_acquired()

        page = 1
        per_page = 100
        total_received = 0
        total_imported = 0
        total_errors = 0
        pages_total = None
        first_payload = None

        while page <= MAX_PULL_PAGES:
            params = {
                "page": page,
                "per_page": per_page,
                "acquired": acquired,
            }
            if start_datetime:
                params["start_datetime"] = start_datetime  # YYYYMMDDHHIISS
            if end_datetime:
                params["end_datetime"] = end_datetime

            # --- Chiamata HTTP reale (il token è negli header del trasporto) ---
            try:
                response = self.transport.request("GET", "/orders", params=params)
            except TransportError as exc:
                # Errore di rete: log e stop (non si conosce lo stato remoto).
                self._log(JobLog, company, "error",
                          "Errore di rete durante il pull: %s" % exc)
                return False

            body = response.json if isinstance(response.json, dict) else {}

            # BricoBravo segnala gli errori con la chiave "errors" (spesso anche
            # con HTTP 200). Trattiamoli come errore esplicito.
            if body.get("errors"):
                self._log(JobLog, company, "error",
                          "Errore BricoBravo: %s" % _truncate(json.dumps(body["errors"])))
                return False

            # Risposta HTTP non 2xx senza chiave "errors": logghiamo status + testo.
            if not response.ok:
                self._log(JobLog, company, "error",
                          "HTTP %s dalla API BricoBravo: %s"
                          % (response.status_code, _truncate(response.text or "")))
                return False

            data = body.get("data") or []
            pagination = body.get("pagination") or {}
            pages_total = pagination.get("pages") or pages_total

            # Salva il payload del PRIMO ordine ricevuto (forma reale dei dati).
            if first_payload is None and data:
                first_payload = _truncate(json.dumps(data[0], ensure_ascii=False))

            total_received += len(data)

            # STRATO 2: importa ogni ordine come sale.order (idempotente).
            for external_order in data:
                result = self.import_order(external_order)
                if result:
                    total_imported += 1
                else:
                    total_errors += 1

            # Condizioni di stop: pagina vuota, ultima pagina raggiunta, o
            # pagina non piena.
            if not data:
                break
            if pages_total and page >= pages_total:
                break
            if len(data) < per_page:
                break
            page += 1

        # Aggiorna l'ora dell'ultimo pull.
        channel.last_pull = fields.Datetime.now()

        if total_received == 0:
            self._log(JobLog, company, "success", "Nessun ordine da scaricare")
        else:
            self._log(
                JobLog, company,
                "success" if total_errors == 0 else "error",
                "Ricevuti %s ordini (importati %s, errori %s), pagine %s"
                % (total_received, total_imported, total_errors, pages_total or page),
                payload=first_payload,
            )
        return total_received

    def _log(self, JobLog, company, result, message, payload=None):
        """Helper: scrive un record in centrivo.job.log per il pull.

        Non logga MAI la api_key/header sh-token: il payload contiene solo dati
        ordine (estratto diagnostico troncato).
        """
        JobLog.create({
            "channel_id": self.channel.id,
            "operation": "pull_orders",
            "result": result,
            "message": message,
            "payload": payload,
            "company_id": company.id,
        })

    # ==================================================================
    # 2) IMPORT DI UN ORDINE
    # ==================================================================
    def import_order(self, external_order):
        """STRATO 2 — traduce un ordine BricoBravo in un vero sale.order in BOZZA.

        ORDINE DELLE OPERAZIONI (vincolante):
          1) RISOLUZIONE PRODOTTI (cascata a 4 livelli, vedi _find_product). Se
             anche un solo prodotto manca -> order.map=error + job.log, NESSUN
             sale.order parziale.
          2) PARTNER: crea/recupera res.partner (dati da shipping_info + dati
             fiscali da invoice_info SE presente; invoice_info null NON blocca).
          3) CREA il sale.order via self.env['sale.order'].create(...) in bozza.
          4) REGISTRA centrivo.order.map (state=imported, sale_order_id).
          5) SOLO ORA chiama mark_acquired(external_id) (strato 3a): l'ordine
             viene marcato acquisito su BricoBravo. Se l'acquired fallisce
             l'import resta valido (acquired_done=False, ritentato dopo).

        IDEMPOTENZA: controllo iniziale su order.map (channel, external_id);
        se già imported -> skip.
        """
        env = self.env
        channel = self.channel
        company = channel.company_id

        # external_id PRIMARIO = id_order (intero). vtex_id_order è un riferimento
        # secondario che salviamo nel client_order_ref del sale.order.
        id_order = external_order.get("id_order") or external_order.get("id")
        vtex_id = external_order.get("vtex_id_order")
        external_id = str(id_order or vtex_id or "")
        if not external_id:
            _logger.warning("Ordine BricoBravo senza identificativo: %s",
                            external_order)
            return False

        OrderMap = env["centrivo.order.map"]
        JobLog = env["centrivo.job.log"]

        # --- IDEMPOTENZA ---------------------------------------------------
        existing = OrderMap.search([
            ("channel_id", "=", channel.id),
            ("external_id", "=", external_id),
        ], limit=1)
        if existing and existing.state == "imported":
            JobLog.create({
                "channel_id": channel.id,
                "operation": "import_order",
                "external_id": external_id,
                "result": "skip",
                "message": "Ordine già importato, saltato (idempotenza).",
                "company_id": company.id,
            })
            return existing.sale_order_id

        # --- 1) RISOLUZIONE PRODOTTI (cascata) -----------------------------
        items = external_order.get("items") or external_order.get("order_items") or []
        order_lines = []
        missing = []
        for item in items:
            product, diag = self._find_product(item)
            if not product:
                # Diagnostica verificabile dai log: cosa è stato cercato e con
                # quanti risultati.
                missing.append(diag or ("EAN %s / code %s" % (
                    item.get("ean") or "—", item.get("product_code") or "—")))
                continue
            order_lines.append((0, 0, {
                "product_id": product.id,
                "product_uom_qty": item.get("quantity") or item.get("qty") or 1,
                # Prezzo dal marketplace; in un affinamento si deciderà se forzarlo
                # o lasciare il listino Odoo.
                "price_unit": item.get("price") or item.get("unit_price") or 0.0,
            }))

        if missing:
            msg = "Prodotto non trovato — %s" % " | ".join(missing)
            self._record_order_error(external_id, msg)
            return False

        if not order_lines:
            self._record_order_error(external_id, "Ordine senza righe/prodotti.")
            return False

        # --- 2) PARTNER (con pulizia dati; invoice_info non bloccante) -----
        partner = self._find_or_create_partner(external_order)
        shipping_partner = self._find_or_create_shipping(external_order, partner)

        # --- 3) CREA (o RIUSA) il sale.order via i modelli Odoo (mai SQL) --
        # RIUSO (anti-duplicato): se questo è un RETRY di un ordine che aveva
        # GIÀ creato il sale.order ma era fallito DOPO (es. conferma fallita al
        # giro precedente), riutilizziamo lo STESSO ordine invece di crearne uno
        # nuovo. Senza questo, un order.map=error con sale_order_id valorizzato
        # genererebbe un sale.order duplicato ad ogni pull.
        status = external_order.get("status")
        status_label = BRICOBRAVO_STATUS_MAP.get(status, "sconosciuto")
        # Riferimento origine per tracciabilità: numero COMMERCIALE del
        # marketplace (vtex_id_order, es. "BB920352542-F1") per primo, id TECNICO
        # (id_order, es. "449023") tra parentesi col prefisso "#" →
        # "BB920352542-F1 (#449023)". La parola "BricoBravo" è rimossa (ridondante:
        # lo indica il team di vendita del canale). Robusto se uno dei due manca.
        ref = self._compose_order_ref(vtex_id, external_id)
        sale_order = existing.sale_order_id if (existing and existing.sale_order_id) \
            else False
        if not sale_order:
            try:
                order_vals = {
                    "partner_id": partner.id,
                    "partner_shipping_id": (shipping_partner or partner).id,
                    "company_id": company.id,
                    "client_order_ref": ref,
                    "origin": ref,
                    # Lo status BricoBravo è annotato (NON pilota lo stato Odoo).
                    "note": "Stato BricoBravo: %s (%s)" % (status, status_label),
                    "order_line": order_lines,
                }
                # Team di vendita del canale (opzionale, campo nativo sale.order):
                # se il canale ha team_id valorizzato lo assegniamo; altrimenti
                # l'ordine entra senza team (comportamento Odoo standard).
                if channel.team_id:
                    order_vals["team_id"] = channel.team_id.id
                sale_order = env["sale.order"].with_company(company).create(order_vals)
            except Exception as exc:  # noqa: BLE001
                self._record_order_error(external_id, "Errore creazione ordine: %s" % exc)
                return False

        # --- 3b) CONFERMA (se il canale lo richiede) -----------------------
        # Gli ordini marketplace sono GIÀ PAGATI: di default si conferma subito
        # via il metodo PUBBLICO action_confirm (mai forzando lo stato a mano).
        # Avviene PRIMA di registrare order.map=imported e PRIMA di mark_acquired
        # (che resta SEMPRE l'ultimo passo, mai anticipato dalla conferma).
        if channel.confirm_on_import and sale_order.state in ("draft", "sent"):
            try:
                sale_order.action_confirm()
            except Exception as exc:  # noqa: BLE001
                # Conferma fallita → NON lasciamo l'ordine in stato ambiguo:
                # registriamo order.map=error MANTENENDO il link al sale.order
                # (il retry riuserà lo stesso ordine, niente duplicati) + log,
                # e NON procediamo ad acquired. L'ordine verrà ritentato al
                # prossimo pull (coerente con la logica di retry esistente:
                # si ritenta tutto ciò che NON è state=imported).
                self._record_order_error(
                    external_id,
                    "Errore conferma ordine %s: %s" % (sale_order.name, exc),
                    sale_order=sale_order)
                return False

        # --- 4) REGISTRA in centrivo.order.map (state=imported) ---------
        map_vals = {
            "channel_id": channel.id,
            "external_id": external_id,
            "sale_order_id": sale_order.id,
            "state": "imported",
            "error_message": False,
            "company_id": company.id,
        }
        if existing:
            existing.write(map_vals)
            order_map = existing
        else:
            order_map = OrderMap.create(map_vals)

        JobLog.create({
            "channel_id": channel.id,
            "operation": "import_order",
            "external_id": external_id,
            "result": "success",
            "message": "Ordine #%s importato → %s (stato: %s)" % (
                external_id, sale_order.name, sale_order.state),
            "company_id": company.id,
        })

        # --- 5) SOLO ORA: mark_acquired su BricoBravo ----------------------
        # SEQUENZA TASSATIVA rispettata: 1) prodotti risolti, 2) partner creato,
        # 3) sale.order creato, 4) order.map=imported registrata → 5) acquired.
        # Se uno dei passi 1-4 fosse fallito NON saremmo arrivati qui (return
        # anticipato): l'ordine resta acquired=0 su BricoBravo e verrà riproposto
        # al prossimo pull, com'è giusto.
        #
        # Il fallimento dell'acquired NON invalida l'import: il sale.order resta,
        # order.map resta imported; acquired_done resta False e l'ordine verrà
        # ritentato (vedi retry_pending_acquired). mark_acquired logga l'esito.
        self.mark_acquired(external_id, order_map=order_map)

        return sale_order

    # ==================================================================
    # 3) MARK ACQUIRED
    # ==================================================================
    def mark_acquired(self, external_id, order_map=None):
        """STRATO 3a — PATCH /orders/{id}/acquired body {"acquired": 1} (REALE).

        Marca l'ordine come ACQUISITO su BricoBravo. Significato deciso da Angelo:
        "acquisito = l'ordine è stato correttamente importato in Odoo". Va quindi
        chiamato SOLO dopo che il sale.order è stato creato E order.map=imported
        (vedi sequenza tassativa in import_order).

        Trasporto: usa self.transport (RestTransport, strato 1) che mette il token
        nell'header sh-token (dalla api_key del channel, MAI nel codice/log) e
        ritenta automaticamente sui 5xx con backoff.

        Gestione risposta BricoBravo:
          - successo : {"data": {"updated": true, "id": "..."}}  → acquired_done=True
          - errore   : {"errors": {...}} (spesso anche con HTTP 200) → log error
          - HTTP non 2xx senza "errors"                          → log error
          - errore di rete (TransportError dopo i retry)         → log error

        IDEMPOTENZA: se order_map.acquired_done è già True, NON richiama la API
        (skip). La chiamata è quindi sicura da ripetere.

        FALLIMENTO NON BLOCCANTE: un acquired fallito NON tocca acquired_done (resta
        False) e NON invalida l'import. L'ordine verrà ritentato (vedi
        retry_pending_acquired) e, finché acquired=0 su BricoBravo, riproposto.

        Ritorna True se l'ordine risulta acquisito (ora o già prima), False se il
        tentativo è fallito.
        """
        channel = self.channel
        OrderMap = self.env["centrivo.order.map"]

        # Recupera la order.map se non passata (es. chiamata diretta/retry).
        if order_map is None:
            order_map = OrderMap.search([
                ("channel_id", "=", channel.id),
                ("external_id", "=", external_id),
            ], limit=1)

        # --- IDEMPOTENZA: già acquisito → non richiamare la API ------------
        if order_map and order_map.acquired_done:
            return True

        # --- Chiamata HTTP reale (PATCH). Token negli header del trasporto;
        # retry sui 5xx già gestito da RestTransport. -----------------------
        try:
            response = self.transport.request(
                "PATCH", "/orders/%s/acquired" % external_id,
                json={"acquired": 1})
        except TransportError as exc:
            self._log_acquired(
                external_id, "error",
                "Acquired ordine #%s fallito (rete): %s" % (external_id, exc))
            return False

        body = response.json if isinstance(response.json, dict) else {}

        # Errore esplicito BricoBravo (la chiave "errors" può arrivare con HTTP 200).
        if body.get("errors"):
            self._log_acquired(
                external_id, "error",
                "Acquired ordine #%s rifiutato da BricoBravo: %s"
                % (external_id, _truncate(json.dumps(body["errors"]))))
            return False

        # HTTP non 2xx senza chiave "errors".
        if not response.ok:
            self._log_acquired(
                external_id, "error",
                "Acquired ordine #%s: HTTP %s — %s"
                % (external_id, response.status_code,
                   _truncate(response.text or "")))
            return False

        # Successo: {"data": {"updated": true, "id": "..."}}.
        data = body.get("data") or {}
        if data.get("updated"):
            if order_map:
                order_map.acquired_done = True
            self._log_acquired(
                external_id, "success",
                "Ordine #%s marcato ACQUISITO su BricoBravo." % external_id)
            return True

        # Risposta inattesa (né "errors" né updated): trattala come errore non
        # bloccante (acquired_done resta False, sarà ritentata).
        self._log_acquired(
            external_id, "error",
            "Acquired ordine #%s: risposta inattesa %s"
            % (external_id, _truncate(json.dumps(body))))
        return False

    def retry_pending_acquired(self):
        """Ritenta mark_acquired per gli ordini IMPORTATI ma non ancora acquisiti.

        Meccanismo semplice (documentato nel report): cerca le order.map di questo
        canale con state=imported e acquired_done=False e richiama mark_acquired per
        ciascuna. Invocato all'inizio di pull_orders, PRIMA di scaricare nuovi
        ordini, così gli acquired rimasti indietro (per un errore di rete/HTTP
        precedente) vengono recuperati. Ritorna il numero di ordini recuperati.
        """
        OrderMap = self.env["centrivo.order.map"]
        pending = OrderMap.search([
            ("channel_id", "=", self.channel.id),
            ("state", "=", "imported"),
            ("acquired_done", "=", False),
        ])
        recovered = 0
        for order_map in pending:
            if self.mark_acquired(order_map.external_id, order_map=order_map):
                recovered += 1
        if pending:
            _logger.info("Retry acquired: %s/%s ordini recuperati per il canale %s",
                         recovered, len(pending), self.channel.name)
        return recovered

    def _log_acquired(self, external_id, result, message):
        """Helper: scrive un record mark_acquired in centrivo.job.log.

        Non logga MAI la api_key/header sh-token (sta solo negli header del
        trasporto, che non vengono mai serializzati nel log).
        """
        self.env["centrivo.job.log"].create({
            "channel_id": self.channel.id,
            "operation": "mark_acquired",
            "external_id": external_id,
            "result": result,
            "message": message[:2000],
            "company_id": self.channel.company_id.id,
        })

    # ==================================================================
    # 4) PUSH SHIPMENT (STRATO 3b) — trigger MANUALE
    # ==================================================================
    def push_shipment(self, order_map):
        """STRATO 3b — PATCH /orders/{id}/shipped (corriere + tracking). REALE, MANUALE.

        Comunica a BricoBravo che l'ordine è spedito. Trigger MANUALE (bottone /
        azione server): nessun automatismo, nessun CRON in questo strato.

        DATI LETTI dai modelli PUBBLICI Odoo (core read-only):
          - il sale.order collegato all'order.map;
          - il suo stock.picking in stato `done` con `carrier_tracking_ref`
            valorizzato (campo NATIVO Odoo, scritto da ShipTracker o a mano: qui
            si LEGGE soltanto, non importa chi l'ha scritto);
          - il delivery.carrier del picking, tradotto nel codice marketplace via
            centrivo.carrier.map (channel, carrier, company).

        PRECONDIZIONI (se non rispettate: log su job.log + stop, NESSUNA eccezione):
          - order.map.state == imported e sale_order_id valorizzato;
          - esattamente UN picking done con tracking (multi-collo NON supportato qui);
          - esiste un carrier.map per (channel, carrier del picking, company);
          - il template URL contiene il segnaposto {tracking}.

        ESITO (pattern IDENTICO a mark_acquired): HTTP 200 sempre →
          - data.updated == true               → shipment_pushed=True, log success
          - chiave "errors" (anche con HTTP 200) → log error, shipment_pushed=False
          - HTTP non 2xx / risposta inattesa     → log error
          - errore di rete (TransportError)      → log error
        L'ordine resta ritentabile (shipment_pushed=False) finché non riesce.

        IDEMPOTENZA: se shipment_pushed è già True → skip (log) e ritorna True.
        """
        external_id = order_map.external_id

        # --- IDEMPOTENZA ---------------------------------------------------
        if order_map.shipment_pushed:
            self._log_shipment(external_id, "skip",
                               "Spedizione ordine #%s già comunicata: skip." % external_id)
            return True

        # --- PRECONDIZIONI -------------------------------------------------
        if order_map.state != "imported" or not order_map.sale_order_id:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: ordine non importato o senza "
                "sale.order collegato." % external_id)
            return False

        sale_order = order_map.sale_order_id
        # Picking in stato done CON tracking valorizzato (campo nativo).
        pickings = sale_order.picking_ids.filtered(
            lambda p: p.state == "done" and p.carrier_tracking_ref)
        if not pickings:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: nessuna spedizione pronta "
                "(serve un picking in stato 'done' con carrier_tracking_ref)."
                % external_id)
            return False
        if len(pickings) > 1:
            # Multi-collo: gestione non ancora supportata (vedi nodi aperti).
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: rilevate %s spedizioni con "
                "tracking. Gestione multi-collo non ancora supportata."
                % (external_id, len(pickings)))
            return False
        picking = pickings[0]

        carrier = picking.carrier_id
        if not carrier:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: il picking %s non ha un "
                "corriere (carrier_id) da cui ricavare il codice marketplace."
                % (external_id, picking.name))
            return False

        carrier_map = self.env["centrivo.carrier.map"].search([
            ("channel_id", "=", self.channel.id),
            ("carrier_id", "=", carrier.id),
            ("company_id", "=", self.channel.company_id.id),
        ], limit=1)
        if not carrier_map:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: nessun mapping corriere per "
                "'%s' sul canale. Configura un centrivo.carrier.map."
                % (external_id, carrier.name))
            return False

        # --- COSTRUZIONE BODY ----------------------------------------------
        tracking_number = (picking.carrier_tracking_ref or "").strip()
        template = carrier_map.tracking_url_template or ""
        if "{tracking}" not in template:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s non eseguito: il template URL del mapping "
                "corriere non contiene il segnaposto {tracking} (URL malformato)."
                % external_id)
            return False
        tracking_url = template.replace("{tracking}", tracking_number)

        body = {
            "courier": carrier_map.external_code,
            "tracking_number": tracking_number,
            "tracking_url": tracking_url,
        }

        # --- CHIAMATA HTTP reale (PATCH). Pattern di mark_acquired. ---------
        try:
            response = self.transport.request(
                "PATCH", "/orders/%s/shipped" % external_id, json=body)
        except TransportError as exc:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s fallito (rete): %s" % (external_id, exc))
            return False

        resp_body = response.json if isinstance(response.json, dict) else {}

        # Errore esplicito BricoBravo (la chiave "errors" può arrivare con HTTP 200).
        if resp_body.get("errors"):
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s rifiutato da BricoBravo: %s"
                % (external_id, _truncate(json.dumps(resp_body["errors"]))))
            return False

        # HTTP non 2xx senza chiave "errors".
        if not response.ok:
            self._log_shipment(
                external_id, "error",
                "Push spedizione #%s: HTTP %s — %s"
                % (external_id, response.status_code,
                   _truncate(response.text or "")))
            return False

        # Successo: {"data": {"updated": true, ...}}.
        data = resp_body.get("data") or {}
        if data.get("updated"):
            order_map.shipment_pushed = True
            self._log_shipment(
                external_id, "success",
                "Spedizione ordine #%s comunicata a BricoBravo (corriere %s, "
                "tracking %s)." % (external_id, body["courier"], tracking_number))
            return True

        # Risposta inattesa (né "errors" né updated): errore non bloccante.
        self._log_shipment(
            external_id, "error",
            "Push spedizione #%s: risposta inattesa %s"
            % (external_id, _truncate(json.dumps(resp_body))))
        return False

    def _log_shipment(self, external_id, result, message):
        """Helper: scrive un record push_shipment in centrivo.job.log.

        Non logga MAI la api_key/header sh-token (stanno solo negli header del
        trasporto, mai serializzati nel log).
        """
        self.env["centrivo.job.log"].create({
            "channel_id": self.channel.id,
            "operation": "push_shipment",
            "external_id": external_id,
            "result": result,
            "message": message[:2000],
            "company_id": self.channel.company_id.id,
        })

    # ==================================================================
    # 5) EXPORT — FEED PREZZI/GIACENZE (CSV via URL pubblico)
    # ==================================================================
    def generate_stock_feed(self):
        """Costruisce il CSV prezzi/giacenze e lo SALVA sul canale.

        Odoo è la fonte di verità: si LEGGE soltanto dai prodotti esistenti, non
        si crea/modifica nulla. Selezione prodotti per TAG (channel.export_product_tag_ids):
        se il criterio è vuoto, il feed è VUOTO (sole intestazioni) e si logga un
        avviso (mai un dump dell'intero catalogo). Solo prodotti con `barcode`
        valorizzato (l'EAN è l'identificativo BricoBravo): senza barcode si salta
        e si logga. Prezzi dai listini del canale (API pubblica `_get_product_price`),
        con fallback bidirezionale selling↔discounted; nessun prezzo → riga saltata.
        Quantità da `stock_quantity_type` con `stock_scope` (azienda o somma
        magazzini). Processing Time = `sale_delay` se >0, altrimenti il default
        del canale. Salva su `stock_feed_content` + `stock_feed_generated_at`.
        """
        channel = self.channel
        company = channel.company_id

        # --- Criterio prodotti: se assente → feed vuoto (sole intestazioni) ---
        tags = channel.export_product_tag_ids
        if not tags:
            self._log_export(
                "skip",
                "Criterio prodotti (tag export) non impostato: feed prezzi/"
                "giacenze VUOTO (non si esporta l'intero catalogo).")
            self._save_stock_feed(CsvSerializer().serialize(STOCK_FEED_HEADERS, []))
            return 0

        sell_pl = channel.pricelist_selling_id
        disc_pl = channel.pricelist_discounted_id
        if not sell_pl and not disc_pl:
            self._log_export(
                "error",
                "Nessun listino configurato (né pieno né scontato): feed non "
                "generato.")
            return False

        Product = self.env["product.product"].with_company(company)
        products = Product.search(
            [("product_tmpl_id.product_tag_ids", "in", tags.ids)])

        rows = []
        skipped = 0
        for product in products:
            barcode = (product.barcode or "").strip()
            if not barcode:
                skipped += 1
                _logger.info("Feed: prodotto %s senza barcode, saltato.",
                             product.display_name)
                continue

            sell = self._pricelist_price(sell_pl, product)
            disc = self._pricelist_price(disc_pl, product)
            # Fallback bidirezionale: se manca uno, usa l'altro.
            if sell is None:
                sell = disc
            if disc is None:
                disc = sell
            if sell is None:  # nessuno dei due listini ha dato un prezzo
                skipped += 1
                _logger.info(
                    "Feed: prodotto %s (%s) senza prezzo dai listini, saltato.",
                    product.display_name, barcode)
                continue

            qty = self._available_quantity(product, channel)
            sale_delay = int(product.sale_delay or 0)
            processing = sale_delay if sale_delay > 0 else channel.processing_time_default

            rows.append([
                barcode,
                self._fmt_price(sell),
                self._fmt_price(disc),
                int(round(qty)),
                processing,
                (product.default_code or "").strip(),
            ])

        content = CsvSerializer().serialize(STOCK_FEED_HEADERS, rows)
        self._save_stock_feed(content)
        self._log_export(
            "success",
            "Feed prezzi/giacenze generato: %s righe (saltati %s prodotti "
            "senza barcode/prezzo)." % (len(rows), skipped))
        return len(rows)

    def _pricelist_price(self, pricelist, product):
        """Prezzo unitario del prodotto dal listino (API pubblica). None se assente.

        Usa `pricelist._get_product_price` (API 18.0), MAI query SQL sui prezzi.
        Ritorna None se il listino non è configurato o l'API non restituisce un
        valore numerico.
        """
        if not pricelist:
            return None
        try:
            price = pricelist._get_product_price(product, 1.0)
        except Exception as exc:  # noqa: BLE001 - robustezza per singolo prodotto
            _logger.warning("Prezzo non calcolabile per %s su listino %s: %s",
                            product.display_name, pricelist.display_name, exc)
            return None
        return price if isinstance(price, (int, float)) else None

    def _available_quantity(self, product, channel):
        """Quantità secondo il tipo (channel.stock_quantity_type) e lo scope.

        scope=company: legge il campo computed nativo senza contesto magazzino.
        scope=warehouses: SOMMA il campo letto nel contesto di ciascun magazzino
        (with_context(warehouse=...)). Campi nativi Odoo: qty_available /
        virtual_available / free_qty.
        """
        field_name = channel.stock_quantity_type or "free_qty"
        if channel.stock_scope == "warehouses" and channel.warehouse_ids:
            total = 0.0
            for warehouse in channel.warehouse_ids:
                total += getattr(
                    product.with_context(warehouse=warehouse.id), field_name)
            return total
        return getattr(product, field_name)

    @staticmethod
    def _fmt_price(value):
        """Formatta un prezzo con 2 decimali e punto decimale (formato feed)."""
        return "%.2f" % (value or 0.0)

    def _save_stock_feed(self, content):
        """Salva il CSV pre-generato sul canale (storage del feed)."""
        self.channel.write({
            "stock_feed_content": content,
            "stock_feed_generated_at": fields.Datetime.now(),
        })

    def _log_export(self, result, message, operation="export_stock_feed"):
        """Helper: scrive un record export sul job.log. Nessun segreto/token."""
        self.env["centrivo.job.log"].create({
            "channel_id": self.channel.id,
            "operation": operation,
            "result": result,
            "message": message[:2000],
            "company_id": self.channel.company_id.id,
        })

    # ==================================================================
    # 6) EXPORT — FEED CATALOGO COMPLETO (CSV 23 colonne)
    # ==================================================================
    def generate_catalog_feed(self):
        """Costruisce il CSV catalogo (23 colonne) e lo SALVA sul canale.

        Stessa selezione/prezzi/quantità/processing/barcode del feed prezzi
        (TASK_23). In più popola le colonne DESCRITTIVE secondo la mappatura
        configurabile del canale (Category/ProductName/Url/Product Description),
        il Brand (OCA product_brand, opzionale e dinamico), fino a 10 URL immagine
        (dinamici e opzionali) e l'IVA (dalle imposte cliente, con default canale).
        Odoo è la fonte di verità: si LEGGE soltanto. Salva su catalog_feed_content
        + catalog_feed_generated_at.
        """
        channel = self.channel
        company = channel.company_id

        tags = channel.export_product_tag_ids
        if not tags:
            self._log_export(
                "skip",
                "Criterio prodotti (tag export) non impostato: feed catalogo "
                "VUOTO (non si esporta l'intero catalogo).",
                operation="export_catalog_feed")
            self._save_catalog_feed(
                CsvSerializer().serialize(CATALOG_FEED_HEADERS, []))
            return 0

        sell_pl = channel.pricelist_selling_id
        disc_pl = channel.pricelist_discounted_id
        if not sell_pl and not disc_pl:
            self._log_export(
                "error",
                "Nessun listino configurato (né pieno né scontato): feed catalogo "
                "non generato.", operation="export_catalog_feed")
            return False

        Product = self.env["product.product"].with_company(company)
        products = Product.search(
            [("product_tmpl_id.product_tag_ids", "in", tags.ids)])

        rows = []
        skipped = 0
        for product in products:
            barcode = (product.barcode or "").strip()
            if not barcode:
                skipped += 1
                _logger.info("Catalogo: prodotto %s senza barcode, saltato.",
                             product.display_name)
                continue

            sell = self._pricelist_price(sell_pl, product)
            disc = self._pricelist_price(disc_pl, product)
            if sell is None:
                sell = disc
            if disc is None:
                disc = sell
            if sell is None:
                skipped += 1
                _logger.info(
                    "Catalogo: prodotto %s (%s) senza prezzo, saltato.",
                    product.display_name, barcode)
                continue

            qty = self._available_quantity(product, channel)
            sale_delay = int(product.sale_delay or 0)
            processing = sale_delay if sale_delay > 0 else channel.processing_time_default

            name = self._mapped_value(product, channel.map_field_name) \
                or (product.name or "")
            category = self._mapped_value(product, channel.map_field_category)
            url = self._mapped_value(product, channel.map_field_url)
            description = self._mapped_value(product, channel.map_field_description)
            brand = self._brand_value(product, channel)
            images = self._image_urls(product, channel)
            vat = self._vat_value(product, channel)

            row = [
                barcode,                      # Sku EAN/GTIN
                category,                     # Category
                brand,                        # Brand
                name,                         # ProductName
                url,                          # Url
                self._fmt_weight(product.weight),  # Weight
                description,                  # Product Description
            ]
            # Image URL 1..10 (le mancanti restano vuote)
            for index in range(10):
                row.append(images[index] if index < len(images) else "")
            row += [
                self._fmt_price(sell),        # Selling Price (Price to GPP)
                self._fmt_price(disc),        # Discounted Price
                vat,                          # Vat
                int(round(qty)),              # Available Quantity
                processing,                   # Processing Time
                (product.default_code or "").strip(),  # Product_Code
            ]
            rows.append(row)

        content = CsvSerializer().serialize(CATALOG_FEED_HEADERS, rows)
        self._save_catalog_feed(content)
        self._log_export(
            "success",
            "Feed catalogo generato: %s righe (saltati %s prodotti senza "
            "barcode/prezzo)." % (len(rows), skipped),
            operation="export_catalog_feed")
        return len(rows)

    def _save_catalog_feed(self, content):
        """Salva il CSV catalogo pre-generato sul canale (storage)."""
        self.channel.write({
            "catalog_feed_content": content,
            "catalog_feed_generated_at": fields.Datetime.now(),
        })

    @staticmethod
    def _fmt_weight(value):
        """Formatta il peso in modo compatto (senza zeri inutili)."""
        return ("%g" % (value or 0.0))

    def _mapped_value(self, product, field_record):
        """Legge il valore della colonna da un campo configurato (ir.model.fields).

        - Se il campo non è impostato o non esiste sul prodotto → "".
        - Se è una relazione → display_name (concatenati se multipli).
        - Char/Text/Html restituiti come stringa (l'HTML è mantenuto).
        """
        if not field_record:
            return ""
        field_name = field_record.name
        if field_name not in product._fields:
            return ""
        try:
            value = product[field_name]
        except Exception:  # noqa: BLE001 - robustezza per singolo campo
            return ""
        if value is False or value is None:
            return ""
        if hasattr(value, "_name"):  # recordset (relazione)
            return ", ".join(value.mapped("display_name"))
        return value

    def _brand_value(self, product, channel):
        """Brand via OCA product_brand, DINAMICO e opzionale (vuoto se assente)."""
        if not channel.use_product_brand:
            return ""
        # Verifica a runtime: modello product.brand presente E campo sul prodotto.
        if "product.brand" not in self.env or "product_brand_id" not in product._fields:
            return ""
        brand = product.product_brand_id
        return brand.name if brand else ""

    def _image_urls(self, product, channel):
        """COSTRUISCE gli URL immagine verso la rotta pubblica del controller (max 10).

        Le immagini NON sono servite dal /web/image nativo (su Community puro il
        pubblico riceve il placeholder): vanno alla rotta dedicata del controller
        (TASK_26), che legge i byte in sudo e li serve solo per i prodotti
        esportabili. Gli URL portano lo STESSO export_token del feed (se Angelo lo
        rigenera, cambiano sia gli URL feed sia quelli immagine — coerente).

          - Image URL 1 (principale) = se il prodotto ha image_1920:
            {base_url}/integrations/feed/image/<channel_id>/product/<product_id>?token=<export_token>
          - Image URL 2..10 (galleria) = per ogni product.image (accesso DINAMICO,
            ordinate per sequence, max 9):
            {base_url}/integrations/feed/image/<channel_id>/gallery/<image_id>?token=<export_token>

        `base_url` = `web.base.url` da ir.config_parameter (MAI hardcodato). Se
        `product.image` non esiste (Community puro): solo l'immagine principale,
        con un log UNA volta per generazione.
        """
        base_url = (self.env["ir.config_parameter"].sudo()
                    .get_param("web.base.url") or "").rstrip("/")
        token = channel.export_token or ""
        prefix = "%s/integrations/feed/image/%s" % (base_url, channel.id)
        urls = []

        # 1) Immagine principale (product.product) — serve byte dalla rotta.
        if product.image_1920:
            urls.append("%s/product/%s?token=%s" % (prefix, product.id, token))

        # 2..10) Galleria product.image (accesso dinamico, opzionale).
        if "product.image" in self.env:
            gallery = getattr(
                product.product_tmpl_id, "product_template_image_ids", False)
            if gallery:
                for image in gallery.sorted("sequence"):
                    if not image.image_1920:
                        continue
                    urls.append("%s/gallery/%s?token=%s" % (prefix, image.id, token))
                    if len(urls) >= 10:
                        break
        elif not getattr(self, "_image_model_warned", False):
            _logger.info(
                "product.image non presente: nel feed catalogo solo l'immagine "
                "principale (Image URL 1).")
            self._image_model_warned = True

        return urls[:10]

    def _vat_value(self, product, channel):
        """Aliquota IVA dalle imposte cliente (taxes_id) percentuali; default canale.

        Prende la prima imposta di tipo 'percent' e ne legge la percentuale
        (intero). Se nessuna imposta percentuale → vat_default del canale.
        """
        for tax in product.taxes_id:
            if tax.amount_type == "percent":
                return int(round(tax.amount))
        return int(channel.vat_default or 22)

    # ==================================================================
    # Helpers di mapping (Odoo fonte di verità per i prodotti)
    # ==================================================================
    def _find_product(self, item):
        """Trova la variante prodotto in Odoo con CASCATA a 4 livelli. NON crea.

        Priorità (la mappatura umana viene prima di tutto):
          a) centrivo.sku.map per (channel, external_code) dove external_code
             può essere ean o product_code — è una decisione umana deliberata.
          b) barcode = item.ean, cercato SIA su product.product SIA sul template
             (product_tmpl_id.barcode). L'ean è normalizzato a stringa (TASK_14:
             il bug era che l'ean arrivava come numero JSON e il confronto con il
             campo Char `barcode` non matchava).
          c) default_code = item.product_code (saltato se null), idem su variante
             e template.
          d) niente trovato -> ritorna (False, diagnostica) per il log.

        Ritorna una tupla (product_or_False, diag): diag è una stringa che
        descrive COSA è stato cercato e con quanti risultati (per i log).

        Nota company_id: NON aggiungiamo un filtro company alla ricerca prodotto.
        Su stage (mono-company) i prodotti sono condivisi/visibili e vanno
        trovati; un filtro company troppo stretto era una delle cause sospette
        del bug. In multi-company vero si valuterà un filtro esplicito.
        """
        env = self.env
        Product = env["product.product"]
        # Normalizzazione robusta: l'ean/codice può arrivare come numero JSON.
        ean = self._norm(item.get("ean"))
        product_code = self._norm(item.get("product_code"))  # può essere null
        diag = []

        # a) Mapping esplicito (priorità massima).
        codes = [c for c in (ean, product_code) if c]
        if codes:
            sku = env["centrivo.sku.map"].search([
                ("channel_id", "=", self.channel.id),
                ("external_code", "in", codes),
            ], limit=1)
            diag.append("sku.map(%s): %s" % (codes, "trovato" if sku else "0"))
            if sku:
                return sku.product_id, ""

        # b) Barcode = EAN, su variante E template.
        if ean:
            product = Product.search(
                ["|", ("barcode", "=", ean),
                 ("product_tmpl_id.barcode", "=", ean)],
                limit=1)
            diag.append("barcode=%s su product.product: %s risultati"
                        % (ean, 1 if product else 0))
            if product:
                return product, ""
        else:
            diag.append("ean assente")

        # c) default_code = product_code, solo se NON null, su variante E template.
        if product_code:
            product = Product.search(
                ["|", ("default_code", "=", product_code),
                 ("product_tmpl_id.default_code", "=", product_code)],
                limit=1)
            diag.append("default_code=%s: %s risultati"
                        % (product_code, 1 if product else 0))
            if product:
                return product, ""
        else:
            diag.append("default_code=None")

        # d) Non trovato: ritorna la diagnostica per il log.
        return False, "; ".join(diag)

    def _compose_order_ref(self, commercial_number, technical_id):
        """Compone il client_order_ref: "BB920352542-F1 (#449023)".

        Formato: numero COMMERCIALE del marketplace (vtex_id_order) per primo,
        id TECNICO (id_order) tra parentesi col prefisso "#". La parola
        "BricoBravo" NON viene messa (ridondante: lo indica il team di vendita).

        Robustezza (uno dei due valori può mancare nel payload):
          - entrambi presenti → "BB920352542-F1 (#449023)";
          - solo commerciale  → "BB920352542-F1";
          - solo tecnico      → "#449023";
          - nessuno dei due   → "" (caso limite: l'import si blocca prima, perché
            external_id è obbligatorio).
        """
        commercial = str(commercial_number).strip() if commercial_number else ""
        technical = ("#%s" % technical_id) if technical_id else ""
        if commercial and technical:
            return "%s (%s)" % (commercial, technical)
        return commercial or technical

    def _norm(self, value):
        """Normalizza un codice (ean/product_code) a stringa stripped o None.

        I valori JSON possono arrivare come int (es. ean numerico) o con spazi:
        li portiamo a stringa pulita per confrontarli col campo Char di Odoo.
        """
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _compose_name(self, source):
        """Compone un nome cliente ROBUSTO e ripulito da un dict di dati.

        Priorità: receiver_name → corporate_name/business_name → first+last.
        Tutto normalizzato con _clean (spazi). La separazione persona/azienda
        (quando una ragione sociale finisce nel cognome) è un AFFINAMENTO FUTURO:
        per ora usiamo il nome composito ripulito.
        """
        if not source:
            return ""
        receiver = _clean(source.get("receiver_name"))
        if receiver:
            return receiver
        corporate = _clean(source.get("corporate_name") or source.get("business_name"))
        if corporate:
            return corporate
        full = _clean("%s %s" % (
            source.get("first_name") or "", source.get("last_name") or ""))
        return full

    def _find_or_create_partner(self, external_order):
        """Trova o crea il res.partner. Fonte principale: shipping_info.

        Dati fiscali da invoice_info SE presente (invoice_info null NON blocca:
        si crea il partner senza dati fiscali e l'ordine entra comunque).
        Dedup semplice: per P.IVA se presente, altrimenti per email, altrimenti
        per nome+città. Se la ricerca è incerta si crea un nuovo partner.
        """
        env = self.env
        Partner = env["res.partner"]
        ship = external_order.get("shipping_info") or {}
        info = external_order.get("invoice_info") or {}  # può essere null

        # Nome: preferisci i dati di spedizione; fallback ai dati fattura.
        name = self._compose_name(ship) or self._compose_name(info) \
            or "Cliente BricoBravo"

        # TASK_48 — email/cellulare: nel payload REALE di produzione stanno DENTRO
        # shipping_info (customer_first_email / customer_first_phone). Catena di
        # fallback: prima shipping_info, poi livello ordine (ordini storici/sandbox),
        # infine i vecchi campi email/phone di ship/info per robustezza.
        email = _clean(
            ship.get("customer_first_email")
            or external_order.get("customer_first_email")
            or ship.get("email") or info.get("email"))
        # Il numero cliente di BricoBravo è un CELLULARE → va sul campo `mobile` di
        # res.partner (NON `phone`). L'alias/mascheramento del marketplace è valido
        # e si salva COSÌ COM'È (in produzione è reale), senza scarti né errori.
        mobile = _clean(
            ship.get("customer_first_phone")
            or external_order.get("customer_first_phone")
            or ship.get("phone") or ship.get("telephone"))
        city = _clean(ship.get("city"))
        vat = info.get("vat_code")

        # --- Dedup partner (logica semplice, documentata) ------------------
        # Se troviamo un partner esistente, RIEMPIamo email/cellulare SOLO se
        # mancanti (non sovrascriviamo dati esistenti con valori sandbox/mascherati).
        if vat:
            partner = Partner.search([("vat", "=", vat)], limit=1)
            if partner:
                return self._fill_contact_if_empty(partner, email, mobile)
        if email:
            partner = Partner.search([("email", "=", email)], limit=1)
            if partner:
                return self._fill_contact_if_empty(partner, email, mobile)
        if name and city:
            partner = Partner.search(
                [("name", "=", name), ("city", "=", city)], limit=1)
            if partner:
                return self._fill_contact_if_empty(partner, email, mobile)

        # --- Creazione nuovo partner (con indirizzo da shipping_info) ------
        vals = {
            "name": name,
            "company_type": "company" if info.get("is_corporate") else "person",
            "vat": vat or False,
            "email": email or False,
            "mobile": mobile or False,
            "company_id": self.channel.company_id.id,
        }
        vals.update(self._address_vals(ship))

        # Dati fiscali da invoice_info SOLO se presente e SOLO se i campi
        # esistono (l10n_it / l10n_it_edi installati).
        if info:
            self._set_if_field(vals, Partner, "l10n_it_codice_fiscale",
                               info.get("fiscal_code"))
            self._set_if_field(vals, Partner, "l10n_it_pec_email",
                               info.get("sdi_pec"))
            self._set_if_field(vals, Partner, "l10n_it_pa_index",
                               info.get("send_invoice_to"))
        return Partner.create(vals)

    def _fill_contact_if_empty(self, partner, email, mobile):
        """Riempie email/cellulare su un partner esistente SOLO se mancanti.

        Non sovrascrive dati già presenti (evita di rimpiazzare un valore reale
        con uno sandbox/mascherato). Il cellulare va sul campo `mobile` (NON
        `phone`). Ritorna il partner.
        """
        updates = {}
        if email and not partner.email:
            updates["email"] = email
        if mobile and not partner.mobile:
            updates["mobile"] = mobile
        if updates:
            partner.write(updates)
        return partner

    def _find_or_create_shipping(self, external_order, parent_partner):
        """Crea l'indirizzo di spedizione da shipping_info (type=delivery)."""
        Partner = self.env["res.partner"]
        ship = external_order.get("shipping_info") or {}
        if not ship:
            return False
        vals = {
            "name": self._compose_name(ship) or parent_partner.name,
            "type": "delivery",
            "parent_id": parent_partner.id,
            "phone": _clean(ship.get("phone") or ship.get("telephone")) or False,
            "company_id": self.channel.company_id.id,
        }
        vals.update(self._address_vals(ship))
        return Partner.create(vals)

    def _address_vals(self, ship):
        """Estrae i campi indirizzo da shipping_info, con pulizia.

        street = street/address + number; zip, city; provincia (es. "VR") mappata
        su res.country.state IT se trovata; country = Italia.
        """
        if not ship:
            return {}
        street_parts = [
            _clean(ship.get("street") or ship.get("address")),
            _clean(ship.get("number") or ship.get("house_number")),
        ]
        street = " ".join(p for p in street_parts if p)
        vals = {
            "street": street or False,
            "zip": _clean(ship.get("postal_code") or ship.get("zip")) or False,
            "city": _clean(ship.get("city")) or False,
        }
        # Country Italia (i dati BricoBravo sono italiani).
        country = self.env.ref("base.it", raise_if_not_found=False)
        if country:
            vals["country_id"] = country.id
            # Provincia: mappa il codice (es. "VR") su res.country.state IT.
            prov = _clean(ship.get("state") or ship.get("province"))
            if prov:
                state = self.env["res.country.state"].search([
                    ("country_id", "=", country.id),
                    ("code", "=", prov.upper()),
                ], limit=1)
                if state:
                    vals["state_id"] = state.id
                # Se non trovata, si lascia il campo libero (nessun blocco).
        return vals

    # ==================================================================
    # Utility
    # ==================================================================
    def _set_if_field(self, vals, model, field_name, value):
        """Imposta vals[field_name]=value solo se il campo esiste sul modello.

        Evita errori se un modulo di localizzazione (l10n_it*) non è installato.
        """
        if value and field_name in model._fields:
            vals[field_name] = value

    def _record_order_error(self, external_id, message, sale_order=None):
        """Registra un errore di import su order.map (state=error) + job.log.

        `sale_order` opzionale: quando l'errore avviene DOPO la creazione del
        sale.order (es. conferma fallita), lo si passa qui per MANTENERE il link
        su order.map.sale_order_id. Così un eventuale retry riusa lo stesso
        ordine invece di crearne uno nuovo (anti-duplicato).
        """
        env = self.env
        channel = self.channel
        company = channel.company_id
        OrderMap = env["centrivo.order.map"]
        existing = OrderMap.search([
            ("channel_id", "=", channel.id),
            ("external_id", "=", external_id),
        ], limit=1)
        vals = {
            "channel_id": channel.id,
            "external_id": external_id,
            "state": "error",
            "error_message": message,
            "company_id": company.id,
        }
        if sale_order:
            vals["sale_order_id"] = sale_order.id
        if existing:
            existing.write(vals)
        else:
            OrderMap.create(vals)
        env["centrivo.job.log"].create({
            "channel_id": channel.id,
            "operation": "import_order",
            "external_id": external_id,
            "result": "error",
            "message": message[:2000],
            "company_id": company.id,
        })
        _logger.warning("Import ordine %s in errore: %s", external_id, message)
