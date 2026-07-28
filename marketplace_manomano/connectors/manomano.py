# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Connettore ManoMano — STRATO 1+2: export offerte (crea e aggiorna).

API ManoMano Partners (REST):
  - Base URL produzione : https://partnersapi.manomano.com
  - Base URL sandbox    : https://partnersapi.sandbox.manomano.com
  - Auth   : API key negli header (letta da channel.api_key; MAI nel codice).
  - Offerte: `PUT /api/v1/offers?seller_contract_id=<id>` (upsert completo) —
    body ARRAY di offerte per SKU (prezzo/stock/spedizione/imballo). L'EAN NON
    entra nel payload offerta: serve solo per la verifica separata a catalogo
    (product-catalog/seller-catalog/api/v1/products/ean/search).

Rif. `docs/manomano-api-reference.md` §Offerte per lo schema reale. La
STRUTTURA è isolata nei metodi _send_offers_batch / _build_offer_item, così
un'eventuale rifinitura futura tocca un solo punto.

Principio: Odoo è la fonte di verità. Sola lettura sui prodotti; si spinge verso
ManoMano. La api_key non viene mai loggata.
"""
import json
import logging
import time

from odoo import fields

from odoo.addons.integrations_core.connectors.base import (
    MarketplaceConnector,
    register_connector,
)
from odoo.addons.integrations_core.connectors.transport import (
    RestTransport,
    TransportError,
)

from .taxonomy_parser import is_relevant

_logger = logging.getLogger(__name__)

MANOMANO_URL_SANDBOX = "https://partnersapi.sandbox.manomano.com"
MANOMANO_URL_PRODUCTION = "https://partnersapi.manomano.com"

# Header di autenticazione ManoMano (confermato da doc reale, non "Authorization").
MANOMANO_AUTH_HEADER = "x-api-key"

# Dimensione massima del blocco SKU per chiamata PUT /api/v1/offers.
# LIMITE REALE CONFERMATO DALL'API (2026-07-28): con 100 offerte per chiamata
# ManoMano risponde HTTP 400 ERR_1405 "maximum 20 offers per request".
# NON alzare questo valore: il tetto è imposto da loro, non è prudenza nostra.
OFFERS_BATCH_SIZE = 20

# Pausa fra un blocco di offerte e il successivo. Il tetto di 20 sopra moltiplica
# per cinque il NUMERO di chiamate, e l'API sta dietro Cloudflare: sparate di
# fila senza respiro fanno scattare il blocco 429 (error_code 1015). Un secondo
# di pausa tiene il ritmo sotto le 60 chiamate al minuto.
OFFERS_BATCH_PAUSE = 1.0

# Gestione del 429 (Cloudflare "You are being rate limited"). La risposta porta
# `retry_after` in secondi; loro raccomandano di raddoppiare l'attesa a ogni
# tentativo e di fermarsi dopo 5. Il tetto per singola attesa evita che un
# valore assurdo tenga occupato un worker Odoo all'infinito.
RATE_LIMIT_STATUS = 429
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_DEFAULT_WAIT = 30
RATE_LIMIT_MAX_WAIT = 120

# Quanti SKU per chiamata di VERIFICA offerte (GET offer-information): gli SKU
# viaggiano in query string, quindi il blocco è più piccolo dell'invio.
OFFER_INFO_BATCH_SIZE = 50

# Campi per pagina nella Taxonomy API (il massimo documentato è 50).
TAXONOMY_PAGE_SIZE = 50

# Limite di sicurezza sulle pagine: evita cicli infiniti se la paginazione
# della risposta fosse incoerente.
TAXONOMY_MAX_PAGES = 50

# Troncamento del payload scritto sul log diagnostico (centrivo.job.log.payload).
PAYLOAD_LOG_LIMIT = 8000

# Tipo documento accettato da ManoMano per il caricamento fattura.
MANOMANO_DOCUMENT_TYPE_INVOICE = "INVOICE"

# Corrieri ammessi da ManoMano — NOMI UFFICIALI dalla loro "Lista dei corrieri"
# (PDF fornito da Angelo, 2026-07-28). Il valore da inviare nel campo `carrier`
# della spedizione è il NOME COSÌ COM'È, non un codice: l'esempio ufficiale
# dell'API porta infatti `"carrier": "UPS"` (maiuscolo), coerente con la lista.
#
# Attenzione a due punti su cui la nostra lista precedente sbagliava, e che
# avrebbero fatto fallire il push alla prima spedizione vera:
#   - le sigle vanno MAIUSCOLE (BRT, GLS, DHL, UPS, TNT, DPD), non minuscole;
#   - "Poste Italiane" è il nome per esteso: `poste` non esiste.
# Rimosso anche il valore generico "other": nella lista ufficiale NON c'è, e
# mandarlo significherebbe farsi rifiutare la spedizione. Per un corriere non in
# elenco si scrive a Transport@manomano.com (indicato nel documento).
#
# La lista è per NOME, non filtrata per paese: il documento indica anche i paesi
# di origine serviti da ciascun corriere, ma quel dato riguarda la scelta
# commerciale del venditore, non la validità del valore.
# Esposta come carrier_codes per il Selection dinamico di centrivo.carrier.map.
MANOMANO_CARRIERS = [
    ("Ader", "Ader"),
    ("Amazon Logistics", "Amazon Logistics"),
    ("APC Overnight", "APC Overnight"),
    ("Asendia", "Asendia"),
    ("Austrian Post", "Austrian Post"),
    ("Baudoin", "Baudoin"),
    ("Bpost", "Bpost"),
    ("BRT", "BRT"),
    ("CBL Logistica", "CBL Logistica"),
    ("CHRONO 13", "CHRONO 13"),
    ("Chronopost", "Chronopost"),
    ("Colis Privé", "Colis Privé"),
    ("Colissimo", "Colissimo"),
    ("Correos", "Correos"),
    ("Correos Express", "Correos Express"),
    ("CTT Express", "CTT Express"),
    ("Dachser", "Dachser"),
    ("DB Schenker", "DB Schenker"),
    ("Deutsche Post", "Deutsche Post"),
    ("DHL", "DHL"),
    ("DHL Freight", "DHL Freight"),
    ("DHL Parcel", "DHL Parcel"),
    ("DPD", "DPD"),
    ("DSV", "DSV"),
    ("Ducros", "Ducros"),
    ("DUSCHEXPRESS", "DUSCHEXPRESS"),
    ("DX", "DX"),
    ("Envialia", "Envialia"),
    ("Evri", "Evri"),
    ("Fedex", "Fedex"),
    ("Fercam", "Fercam"),
    ("France Express", "France Express"),
    ("Furdeco", "Furdeco"),
    ("Gebruder Weiss", "Gebruder Weiss"),
    ("Gefco", "Gefco"),
    ("Gel", "Gel"),
    ("Geodis", "Geodis"),
    ("GLS", "GLS"),
    ("Heppner", "Heppner"),
    ("Hermes", "Hermes"),
    ("Kuehne Nagel", "Kuehne Nagel"),
    ("La Poste Suivi", "La Poste Suivi"),
    ("Liccardi", "Liccardi"),
    ("Marmeth", "Marmeth"),
    ("Mazet", "Mazet"),
    ("Mondial Relay", "Mondial Relay"),
    ("MRW", "MRW"),
    ("NACEX", "NACEX"),
    ("OnTime", "OnTime"),
    ("Palletways", "Palletways"),
    ("Pallex", "Pallex"),
    ("ParcelForce", "ParcelForce"),
    ("Post NL", "Post NL"),
    ("Poste Italiane", "Poste Italiane"),
    ("Prévoté", "Prévoté"),
    ("Raben", "Raben"),
    ("Relais Colis", "Relais Colis"),
    ("Royal Mail", "Royal Mail"),
    ("Sanidis", "Sanidis"),
    ("SDA", "SDA"),
    ("Sending", "Sending"),
    ("Seur", "Seur"),
    ("Spring", "Spring"),
    ("TDN", "TDN"),
    ("TIPSA", "TIPSA"),
    ("TNT", "TNT"),
    ("Transaher", "Transaher"),
    ("Tred Chariot", "Tred Chariot"),
    ("TRS", "TRS"),
    ("Trusk", "Trusk"),
    ("UDEL", "UDEL"),
    ("UK Mail", "UK Mail"),
    ("UPS", "UPS"),
    ("viaxpress", "viaxpress"),
    ("VIR", "VIR"),
    ("Whistl", "Whistl"),
    ("XDP", "XDP"),
    ("XPO", "XPO"),
    ("Yodel", "Yodel"),
    ("Zeleris", "Zeleris"),
]

# Mappa degli stati ordine ManoMano (MAIUSCOLO, rif. docs/manomano-api-reference.md
# §Ordini) verso un'etichetta interna leggibile.
MANOMANO_ORDER_STATUS_MAP = {
    "PENDING": "in attesa",
    "ACCEPTED": "accettato",
    "SHIPPED": "spedito",
    "REFUSED": "rifiutato",
    "CANCELLED": "annullato",
    "CLOSED": "chiuso",
}

# Colonne del feed prodotto ManoMano (ordine UFFICIALE dal modello
# docs/manomano-feed-modello-prodotto.xlsx, foglio "Feed"). Header del CSV.
MANOMANO_FEED_COLUMNS = [
    "sku", "ean", "sku_manufacturer", "brand", "manufacturer",
    "merchant_category", "title", "description", "product_url",
    "image_1", "image_2", "image_3", "image_4", "image_5",
    "cross_sell_sku", "Sample_SKU",
    "manufacturer_pdf", "product_information_pdf", "repairability_index_pdf",
    "product_instructions_pdf", "safety_information_pdf",
    "refrigeration_devices_information_pdf", "eu_energy_efficiency_class_url",
    "unit_count", "unit_count_type", "ParentSKU", "parent_title",
    "weight", "weight_unit", "power", "power_unit", "voltage", "voltage_unit",
    "material", "colour", "energy_efficiency_rating", "certification_body",
    "pcs_per_pack", "pcs_per_pack_unit", "origin", "warranty", "warranty_unit",
    "availability_of_spare_parts", "availability_of_spare_parts_unit",
    "environmental_certification", "reparability_index", "reparability_index_unit",
    "primary_packaging_material", "max._energy_efficiency_rating",
    "min._energy_efficiency_rating", "primary_packaging_certification",
    "main_material", "material_2", "material_3",
    "%_main_material", "%_main_material_unit", "%_material_2",
    "%_material_2_unit", "%_material_3", "%_material_3_unit",
    "nf_certification", "mounting_method",
]

# Colonne OBBLIGATORIE (foglio "Definitions"). ean è surrogabile da
# sku_manufacturer+brand: l'avviso lo tratta come coppia alternativa.
MANOMANO_FEED_MANDATORY = {"sku", "brand", "title", "description", "image_1"}


@register_connector("manomano", "ManoMano")
class ManoManoConnector(MarketplaceConnector):
    """Connettore concreto ManoMano (Famiglia A) — export offerte (crea/aggiorna)."""

    # NESSUN URL di default precompilato per l'onchange del canale: se presente,
    # l'onchange di integrations_core precompilerebbe channel.base_url e
    # _resolve_base_url gli darebbe sempre priorità, rendendo muto il cambio
    # Ambiente (sandbox/produzione). Con base_url vuoto, _resolve_base_url
    # sceglie sandbox/produzione da channel.environment (vedi sotto); base_url
    # resta un override manuale esplicito.

    # Lista chiusa dei corrieri ManoMano esposta alla base (carrier_codes) per il
    # Selection dinamico di centrivo.carrier.map. Vedi MANOMANO_CARRIERS.
    carrier_codes = MANOMANO_CARRIERS

    def __init__(self, channel):
        super().__init__(channel)
        base_url = self._resolve_base_url(channel)
        self.transport = RestTransport(
            base_url=base_url,
            default_headers={
                MANOMANO_AUTH_HEADER: channel.api_key or "__API_KEY_DAL_CHANNEL__",
                "x-thirdparty-name": channel.manomano_thirdparty_name or "HDcasa_1.0",
            },
        )
        # Numero di avvisi (celle fuori lista, campi numerici non validi,
        # obbligatori mappati ma vuoti) raccolti nell'ULTIMA generazione del
        # feed prodotto. L'azione sul canale lo legge per scegliere il colore
        # della notifica: senza questo attributo un utente non tecnico non
        # aprirebbe mai il Log operazioni, perché il popup resterebbe sempre
        # verde anche con centinaia di avvisi da rivedere.
        self.product_feed_warnings_count = 0

    @staticmethod
    def _shipping_window(evasione, transito_min, transito_max):
        """Finestra di consegna da mandare a ManoMano, come (minimo, massimo).

        Somma il tempo di evasione del prodotto ai giorni di transito del
        corriere. ManoMano pretende un INTERVALLO: minimo e massimo uguali
        rendono il modello di spedizione non idoneo e l'offerta viene
        rifiutata. Se i valori in ingresso producessero una finestra
        degenere, il massimo viene portato a minimo + 1 come rete di
        sicurezza: meglio una promessa un giorno più larga che un'offerta
        rifiutata.
        """
        evasione = max(0, int(evasione or 0))
        transito_min = max(0, int(transito_min or 0))
        transito_max = max(0, int(transito_max or 0))
        minimo = evasione + transito_min
        massimo = evasione + transito_max
        if massimo <= minimo:
            massimo = minimo + 1
        return minimo, massimo

    @staticmethod
    def _resolve_base_url(channel):
        """URL API in base all'ambiente del canale (base_url manuale ha priorità).

        - se channel.base_url è valorizzato → si usa quello (override esplicito);
        - altrimenti channel.environment: 'production' → URL produzione, ogni
          altro valore (default 'sandbox') → URL sandbox.
        """
        if channel.base_url:
            return channel.base_url
        if channel.environment == "production":
            return MANOMANO_URL_PRODUCTION
        return MANOMANO_URL_SANDBOX

    # ==================================================================
    # STRATO 1+2 — EXPORT OFFERTE (crea/aggiorna, EAN) via update_offers
    # ==================================================================
    def push_offers(self):
        """Crea/aggiorna (upsert) le offerte ManoMano (push a blocchi).

        Odoo è la fonte di verità: sola lettura sui prodotti. Selezione per TAG
        (channel.export_product_tag_ids); se vuoto → nessun invio + log. Serve il
        listino di vendita, la griglia di spedizione (carrier_grid_name) e
        almeno un contract. Per ogni prodotto: SKU = default_code, prezzo dal
        listino, giacenza per tipo/scope. L'offerta è per SKU (l'EAN non entra
        nel payload). Le righe si inviano a blocchi (OFFERS_BATCH_SIZE) per OGNI
        contract configurato via PUT /api/v1/offers.
        """
        channel = self.channel

        # --- Precondizioni -------------------------------------------------
        tags = channel.export_product_tag_ids
        if not tags:
            self._log_offers("skip",
                             "Nessun tag prodotti impostato: nessuna offerta "
                             "inviata (non si esporta l'intero catalogo).")
            return 0
        contracts = channel._manomano_contracts()
        if not contracts:
            self._log_offers("error",
                             "Nessun contract ManoMano configurato sul canale.")
            return False
        if not channel.pricelist_selling_id:
            self._log_offers("error",
                             "Nessun listino di vendita configurato: offerte non "
                             "inviate.")
            return False
        if not (channel.manomano_carrier_grid_name or "").strip():
            self._log_offers("error",
                             "Nessuna griglia di spedizione (carrier_grid_name) "
                             "configurata sul canale: la creazione offerta la "
                             "richiede, nessuna offerta inviata.")
            return False

        Product = self.env["product.product"].with_company(channel.company_id)
        products = Product.search(
            [("product_tmpl_id.product_tag_ids", "in", tags.ids)], order="id")

        rows = self._offer_rows(products, channel)
        if not rows:
            self._log_offers("skip",
                             "Nessuna offerta valida da inviare (prodotti senza "
                             "default_code o prezzo).")
            return 0

        items = [self._build_offer_item(r) for r in rows]

        # --- Invio a blocchi, per contract ---------------------------------
        total_sent = 0
        errors = 0
        for contract in contracts:
            for index, batch in enumerate(self._chunks(items, OFFERS_BATCH_SIZE)):
                # Pausa PRIMA di ogni blocco tranne il primo: con blocchi da 20
                # le chiamate sono tante e ravvicinate, e Cloudflare blocca le
                # raffiche. Meglio un invio più lento che un invio respinto.
                if index and OFFERS_BATCH_PAUSE:
                    time.sleep(OFFERS_BATCH_PAUSE)
                if self._send_offers_batch(batch, contract):
                    total_sent += len(batch)
                else:
                    errors += 1

        self._log_offers(
            "success" if errors == 0 else "error",
            "Offerte ManoMano: %s righe valide, inviate %s (contract: %s), "
            "blocchi in errore %s." % (
                len(items), total_sent, ", ".join(contracts), errors))
        return total_sent

    def _offer_rows(self, products, channel):
        """Costruisce le righe grezze dell'offerta. Salta e logga gli scarti.

        L'offerta è per SKU: l'EAN NON serve più (non entra nel payload di
        PUT /api/v1/offers; l'aggancio a catalogo è una verifica separata).
        Requisiti:
        - SKU (default_code): riferimento seller; senza → saltata.
        - prezzo dal listino di vendita: senza → saltata.
        Il prezzo inviato è il listino di vendita (pricelist_selling_id): si
        vende a quel prezzo. Il prezzo barrato/scontato
        (retail_price_vat_included, che su ManoMano deve essere PIÙ ALTO del
        prezzo pagato) NON si invia per ora: è un affinamento futuro, da
        abilitare solo dopo aver confermato con Angelo la semantica dello
        sconto (retail = prezzo pieno più alto). Il tempo di evasione segue
        sale_delay, con fallback al default del canale; ci si somma il
        transito del corriere configurato sul canale
        (manomano_transit_days_min/max) per ottenere l'INTERVALLO di consegna
        che ManoMano richiede (vedi _shipping_window). carrier_grid_name
        viene dal canale (precondizione verificata in push_offers).
        """
        rows = []
        for product in products:
            sku = (product.default_code or "").strip()
            if not sku:
                _logger.info("ManoMano: prodotto %s senza default_code, saltato.",
                             product.display_name)
                continue
            price = self._pricelist_price(channel.pricelist_selling_id, product)
            if price is None:
                _logger.info("ManoMano: prodotto %s (%s) senza prezzo, saltato.",
                             product.display_name, sku)
                continue
            qty = self._available_quantity(product, channel)
            sale_delay = int(product.sale_delay or 0)
            evasione = (sale_delay if sale_delay > 0
                        else channel.processing_time_default)
            shipping_time_min, shipping_time_max = self._shipping_window(
                evasione, channel.manomano_transit_days_min,
                channel.manomano_transit_days_max)
            rows.append({
                "sku": sku,
                "price": round(float(price), 2),
                "stock": int(round(qty)),
                "weight": product.weight or 0.0,
                "shipping_time_min": shipping_time_min,
                "shipping_time_max": shipping_time_max,
                "carrier_grid_name": channel.manomano_carrier_grid_name,
            })
        return rows

    def _build_offer_item(self, row):
        """Item offerta per PUT /api/v1/offers (rif. docs/manomano-api-reference.md).

        L'EAN NON è nel payload (l'aggancio al catalogo ManoMano è per SKU/verifica
        EAN separata). Prezzo IVA inclusa = listino di vendita (pricelist_selling_id):
        si vende a quel prezzo. Il prezzo barrato/scontato
        (retail_price_vat_included, che su ManoMano deve essere PIÙ ALTO del prezzo
        pagato) NON si invia: è un affinamento futuro, da abilitare solo dopo aver
        confermato con Angelo la semantica dello sconto (retail = prezzo pieno più
        alto). shipping e packaging con default sensati; carrier_grid.name dal
        canale.
        """
        item = {
            "sku": row["sku"],
            "stock": row["stock"],
            "pricing": {"price_vat_included": row["price"]},
            "shipping": {
                "display_weight": row.get("weight") or 0.0,
                "carrier_grid": [{
                    "name": row["carrier_grid_name"],
                    "shipping_time_min": row["shipping_time_min"],
                    "shipping_time_max": row["shipping_time_max"],
                }],
            },
            "packaging": {"min_quantity": 1, "increment": 1},
        }
        return item

    @staticmethod
    def _retry_after_seconds(response, previous=None):
        """Quanti secondi aspettare dopo un 429, dalla risposta stessa.

        Cloudflare mette `retry_after` (secondi) nel corpo JSON. Al primo 429 si
        usa quel valore; dai successivi si raddoppia l'attesa precedente, come
        raccomandano loro. Difensivo: valore assente, non numerico o negativo →
        default; attesa comunque limitata a RATE_LIMIT_MAX_WAIT, perché un
        `retry_after` sballato terrebbe fermo un worker Odoo.
        """
        if previous is not None:
            return min(previous * 2, RATE_LIMIT_MAX_WAIT)
        body = response.json if isinstance(response.json, dict) else {}
        wait = body.get("retry_after")
        if isinstance(wait, bool) or not isinstance(wait, (int, float)):
            wait = RATE_LIMIT_DEFAULT_WAIT
        if wait <= 0:
            wait = RATE_LIMIT_DEFAULT_WAIT
        return min(float(wait), RATE_LIMIT_MAX_WAIT)

    def _request_rate_limited(self, method, path, **kwargs):
        """transport.request che RISPETTA il 429 di Cloudflare.

        `integrations_core` ritenta solo i 5xx (RETRYABLE_STATUS) e non si tocca:
        serve anche BricoBravo. L'attesa sul 429 vive quindi qui, sul solo
        ManoMano. Esaurititi i tentativi si ritorna comunque l'ultima risposta:
        è il chiamante che la logga come errore, con payload e corpo.
        """
        wait = None
        response = None
        for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
            response = self.transport.request(method, path, **kwargs)
            if response.status_code != RATE_LIMIT_STATUS:
                return response
            if attempt == RATE_LIMIT_MAX_RETRIES:
                break
            wait = self._retry_after_seconds(response, wait)
            _logger.warning(
                "ManoMano: rate limit (429) su %s %s, attesa %ss "
                "(tentativo %s/%s).",
                method, path, wait, attempt, RATE_LIMIT_MAX_RETRIES)
            time.sleep(wait)
        return response

    def _send_offers_batch(self, items, contract):
        """PUT /api/v1/offers?seller_contract_id=<contract>, body = array offerte.

        Esito per-SKU in content[].response.status. Ritorna True se la chiamata è
        andata (2xx). In OGNI caso di rifiuto (HTTP o per-SKU) si logga anche il
        PAYLOAD inviato: ManoMano può rispondere "400 Bad Request" senza dire
        quale campo non le piace, quindi l'unico modo per capire è vedere cosa
        abbiamo spedito.
        """
        payload = self._dump(items)
        try:
            response = self._request_rate_limited(
                "PUT", "/api/v1/offers?seller_contract_id=%s" % contract,
                json=items)
        except TransportError as exc:
            self._log_offers("error",
                             "Invio offerte (contract %s) fallito (rete): %s"
                             % (contract, exc),
                             payload=payload)
            return False
        if not response.ok:
            self._log_offers("error",
                             "Invio offerte (contract %s): HTTP %s — %s"
                             % (contract, response.status_code,
                                (response.text or "")[:1000]),
                             payload=payload)
            return False
        rejected = self._rejected_skus(response)
        if rejected:
            # Chiamata 2xx ma singoli SKU rifiutati: senza questo log l'esito
            # complessivo direbbe "tutto ok" nascondendo il rifiuto.
            self._log_offers("error",
                             "Invio offerte (contract %s): chiamata accettata "
                             "ma %s SKU rifiutati da ManoMano — %s"
                             % (contract, len(rejected), "; ".join(rejected)),
                             payload=payload)
        return True

    @staticmethod
    def _rejected_skus(response):
        """SKU rifiutati dentro una risposta 2xx (content[].response.status).

        Difensivo: se la struttura non è quella attesa ritorna lista vuota (la
        diagnosi si fa comunque sul corpo grezzo loggato).
        """
        body = response.json if isinstance(response.json, dict) else {}
        rejected = []
        for entry in body.get("content") or []:
            if not isinstance(entry, dict):
                continue
            status = (entry.get("response") or {}).get("status")
            if isinstance(status, int) and 200 <= status < 300:
                continue
            if status is None:
                continue
            detail = entry.get("errors") or entry.get("warnings")
            line = "%s: %s %s" % (
                entry.get("sku") or "?", status,
                (entry.get("response") or {}).get("reason_phrase") or "")
            if detail:
                line += " — %s" % json.dumps(detail, ensure_ascii=False,
                                             default=str)
            rejected.append(line.strip())
        return rejected

    # ==================================================================
    # DIAGNOSI — verifica offerte su ManoMano (SOLA LETTURA)
    # ==================================================================
    def check_offers(self):
        """Interroga ManoMano sullo stato delle offerte. NON scrive nulla.

        `GET /api/v1/offer-information/offers?seller_contract_id=<id>&skus=a,b`
        (rif. docs/manomano-api-reference.md §Info offerte esistenti) restituisce,
        per ogni SKU: prezzo e giacenza REGISTRATI da loro, lo stato
        (COMPLETED/ERROR), la lista errori e l'id prodotto ManoMano.

        Serve a rispondere a "perché l'offerta è rifiutata/offline?" quando il
        PUT risponde 400 senza motivo: confronta ciò che crediamo di aver
        mandato con ciò che ManoMano ha davvero in casa.

        Il CORPO GREZZO della risposta viene sempre scritto nel payload del log:
        è la misura vera, indipendente dalla nostra interpretazione dei campi
        (i nomi esatti nella risposta sono da confermare sul reale).

        Ritorna il numero di SKU interrogati.
        """
        channel = self.channel
        contracts = channel._manomano_contracts()
        if not contracts:
            self._log_offers("error",
                             "Nessun contract ManoMano configurato sul canale: "
                             "verifica offerte non eseguita.",
                             operation="check_offers")
            return False
        tags = channel.export_product_tag_ids
        if not tags:
            self._log_offers("skip",
                             "Nessun tag prodotti impostato: nessuna offerta da "
                             "verificare.",
                             operation="check_offers")
            return 0

        Product = self.env["product.product"].with_company(channel.company_id)
        products = Product.search(
            [("product_tmpl_id.product_tag_ids", "in", tags.ids)], order="id")
        skus = []
        for product in products:
            sku = (product.default_code or "").strip()
            if sku and sku not in skus:
                skus.append(sku)
        if not skus:
            self._log_offers("skip",
                             "Nessuno SKU (riferimento interno) da verificare.",
                             operation="check_offers")
            return 0

        for contract in contracts:
            for index, batch in enumerate(
                    self._chunks(skus, OFFER_INFO_BATCH_SIZE)):
                if index and OFFERS_BATCH_PAUSE:
                    time.sleep(OFFERS_BATCH_PAUSE)
                self._check_offers_batch(batch, contract)
        return len(skus)

    def _check_offers_batch(self, skus, contract):
        """Una chiamata di verifica (sola lettura) + log dell'esito grezzo."""
        try:
            response = self._request_rate_limited(
                "GET", "/api/v1/offer-information/offers",
                params={"seller_contract_id": contract,
                        "skus": ",".join(skus)})
        except TransportError as exc:
            self._log_offers("error",
                             "Verifica offerte (contract %s) fallita (rete): %s"
                             % (contract, exc),
                             operation="check_offers")
            return False
        raw = response.text or ""
        if not response.ok:
            self._log_offers("error",
                             "Verifica offerte (contract %s): HTTP %s — %s"
                             % (contract, response.status_code, raw[:1000]),
                             payload=raw, operation="check_offers")
            return False
        self._log_offers(
            "success",
            "Verifica offerte (contract %s, %s SKU): %s"
            % (contract, len(skus), self._summarize_offer_info(response)),
            payload=raw, operation="check_offers")
        return True

    @staticmethod
    def _summarize_offer_info(response):
        """Riassunto leggibile della risposta di verifica (difensivo).

        I nomi dei campi sono quelli documentati (sku/status/price/stock/errors/
        id_me): se la struttura reale differisce, il riassunto degrada a "non
        interpretabile" e resta valido il corpo grezzo nel payload del log.
        """
        body = response.json
        entries = body.get("content") if isinstance(body, dict) else body
        if not isinstance(entries, list) or not entries:
            return ("nessuna offerta restituita (vedi il corpo grezzo nel "
                    "campo Payload)")
        parts = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            errors = entry.get("errors")
            parts.append(
                "%s → stato %s, prezzo %s, giacenza %s%s%s" % (
                    entry.get("sku") or "?",
                    entry.get("status") or "?",
                    entry.get("price") if entry.get("price") is not None else "?",
                    entry.get("stock") if entry.get("stock") is not None else "?",
                    ", id ManoMano %s" % entry["id_me"] if entry.get("id_me")
                    else "",
                    ", ERRORI: %s" % json.dumps(errors, ensure_ascii=False,
                                                default=str) if errors else ""))
        if not parts:
            return ("risposta non interpretabile (vedi il corpo grezzo nel "
                    "campo Payload)")
        return " | ".join(parts)

    def _pricelist_price(self, pricelist, product):
        """Prezzo unitario dal listino (API pubblica 18.0). None se assente."""
        if not pricelist:
            return None
        try:
            price = pricelist._get_product_price(product, 1.0)
        except Exception as exc:  # noqa: BLE001 - robustezza per singolo prodotto
            _logger.warning("Prezzo non calcolabile per %s: %s",
                            product.display_name, exc)
            return None
        return price if isinstance(price, (int, float)) else None

    def _available_quantity(self, product, channel):
        """Quantità secondo channel.stock_quantity_type e stock_scope."""
        field_name = channel.stock_quantity_type or "free_qty"
        if channel.stock_scope == "warehouses" and channel.warehouse_ids:
            total = 0.0
            for warehouse in channel.warehouse_ids:
                total += getattr(
                    product.with_context(warehouse=warehouse.id), field_name)
            return total
        return getattr(product, field_name)

    @staticmethod
    def _chunks(seq, size):
        """Spezza una lista in blocchi di al più `size` elementi."""
        for start in range(0, len(seq), size):
            yield seq[start:start + size]

    def _log_offers(self, result, message, payload=None,
                    operation="push_offers"):
        """Scrive un record su centrivo.job.log. Nessun segreto.

        `payload` finisce nel campo omonimo del log (troncato): serve alla
        diagnosi — senza sapere COSA abbiamo spedito, un "HTTP 400" non è
        interpretabile. Il payload offerte contiene solo sku/prezzo/giacenza/
        spedizione: nessuna credenziale (la api_key viaggia negli header, che
        non logghiamo MAI).
        """
        vals = {
            "channel_id": self.channel.id,
            "operation": operation,
            "result": result,
            "message": message[:2000],
            "company_id": self.channel.company_id.id,
        }
        if payload:
            vals["payload"] = payload[:PAYLOAD_LOG_LIMIT]
        self.env["centrivo.job.log"].create(vals)

    @staticmethod
    def _dump(value):
        """Serializza per il log diagnostico (mai solleva: è uno strumento)."""
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001 - il log non deve mai rompere il flusso
            return str(value)

    # ==================================================================
    # ORDINI — helper puri e parsing del formato ManoMano (isolato)
    # ==================================================================
    @staticmethod
    def _norm(value):
        """Normalizza un codice (sku/ean) a stringa stripped o None.

        I valori JSON possono arrivare come numero o con spazi: li portiamo a
        stringa pulita per il confronto con i campi Char di Odoo.
        """
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _clean_txt(value):
        """Ripulisce una stringa anagrafica (collassa spazi, strip). '' se vuota."""
        if not value:
            return ""
        return " ".join(str(value).split())

    def _mm_order_id(self, external_order):
        """External id dell'ordine ManoMano = order_reference (rif. API §Ordini)."""
        return str(external_order.get("order_reference") or "")

    def _mm_status(self, external_order):
        """Etichetta leggibile dello stato ordine ManoMano (status, MAIUSCOLO)."""
        raw = external_order.get("status")
        return MANOMANO_ORDER_STATUS_MAP.get(raw, "sconosciuto"), raw

    def _mm_order_lines(self, external_order):
        """Righe grezze dell'ordine → lista di dict {ean, sku, qty, price}.

        Da `products` (rif. API §Ordini): per riga `seller_sku`, `quantity`,
        `price: {amount, currency}`. Le righe ordine NON hanno EAN (si risolve
        per seller_sku): ean sempre None.
        """
        lines = external_order.get("products") or []
        result = []
        for line in lines:
            result.append({
                "ean": None,
                "sku": self._norm(line.get("seller_sku")),
                "qty": line.get("quantity") or 1,
                "price": (line.get("price") or {}).get("amount") or 0.0,
            })
        return result

    def _mm_partner_data(self, external_order):
        """Dati cliente/indirizzo grezzi → dict normalizzato.

        Da `addresses.shipping` (rif. API §Ordini): firstname/lastname
        (fallback su `customer`), email, phone, indirizzo (address_line1 +
        eventuali address_line2/3), zipcode, city. Nessuna provincia (state
        sempre ""); country_iso passato a parte per la risoluzione del paese.
        Partita IVA da billing_fiscal_number o vat.local_vat_number.
        """
        order = external_order or {}
        ship = ((order.get("addresses") or {}).get("shipping") or {})
        customer = order.get("customer") or {}
        name = self._clean_txt(
            "%s %s" % (ship.get("firstname") or customer.get("firstname") or "",
                       ship.get("lastname") or customer.get("lastname") or ""))
        street = self._clean_txt(ship.get("address_line1"))
        for extra_key in ("address_line2", "address_line3"):
            extra = self._clean_txt(ship.get(extra_key))
            if extra:
                street = "%s, %s" % (street, extra) if street else extra
        vat = (order.get("billing_fiscal_number")
               or (order.get("vat") or {}).get("local_vat_number"))
        return {
            "name": name or "Cliente ManoMano",
            "email": self._clean_txt(ship.get("email")),
            "phone": self._clean_txt(ship.get("phone")),
            "street": street,
            "zip": self._clean_txt(ship.get("zipcode")),
            "city": self._clean_txt(ship.get("city")),
            "state": "",
            "country_iso": self._clean_txt(ship.get("country_iso")),
            "vat": self._clean_txt(vat),
        }

    # ==================================================================
    # ORDINI — accettazione (gemello mark_acquired), automatica dopo l'import
    # ==================================================================
    def _first_contract_id(self):
        """Primo contract del canale come intero, o None se assente/non numerico.

        Il seller_contract_id di ManoMano è un intero. Se il campo canale
        contiene un valore non numerico (es. un vecchio "IT"), ritorna None
        invece di sollevare: il chiamante logga un errore chiaro e prosegue
        (fallimento non bloccante). Multi-contract: si usa il primo (mapping
        ordine→contract è un affinamento futuro).
        """
        contracts = self.channel._manomano_contracts()
        if not contracts:
            return None
        try:
            return int(contracts[0])
        except (TypeError, ValueError):
            return None

    def accept_order(self, external_id, order_map=None):
        """Accetta l'ordine su ManoMano. Idempotente su acquired_done.

        Chiamato SOLO dopo che il sale.order è creato e order.map=imported.
        Fallimento NON bloccante: acquired_done resta False, ritentato al pull
        successivo. Endpoint reale: `POST /orders/v1/accept-orders`, body ARRAY
        `[{"order_reference", "seller_contract_id"}]` (rif.
        docs/manomano-api-reference.md §"Accetta ordini (batch)").
        """
        OrderMap = self.env["centrivo.order.map"]
        if order_map is None:
            order_map = OrderMap.search([
                ("channel_id", "=", self.channel.id),
                ("external_id", "=", external_id),
            ], limit=1)
        if order_map and order_map.acquired_done:
            return True

        contract_id = self._first_contract_id()
        if contract_id is None:
            self._log_op("accept_order", external_id, "error",
                         "Contract ManoMano assente o non numerico sul canale.")
            return False

        body = [{
            "order_reference": external_id,
            "seller_contract_id": contract_id,
        }]
        try:
            response = self.transport.request(
                "POST", "/orders/v1/accept-orders", json=body)
        except TransportError as exc:
            self._log_op("accept_order", external_id, "error",
                         "Accettazione ordine #%s fallita (rete): %s"
                         % (external_id, exc))
            return False
        if not response.ok:
            self._log_op("accept_order", external_id, "error",
                         "Accettazione ordine #%s: HTTP %s — %s"
                         % (external_id, response.status_code,
                            (response.text or "")[:500]))
            return False
        if order_map:
            order_map.acquired_done = True
        self._log_op("accept_order", external_id, "success",
                     "Ordine #%s accettato su ManoMano (contract %s)."
                     % (external_id, contract_id))
        return True

    def refuse_order(self, external_id, order_map=None):
        """Rifiuta l'ordine su ManoMano. Idempotente su manomano_refused.

        Azione MANUALE e irreversibile: la decide Angelo caso per caso, non c'è
        automatismo né cron. Endpoint reale: `POST /orders/v1/refuse-orders`,
        body ARRAY `[{"order_reference", "seller_contract_id"}]` — stesso
        formato dell'accettazione (rif. docs/manomano-api-reference.md §Ordini).
        """
        OrderMap = self.env["centrivo.order.map"]
        if order_map is None:
            order_map = OrderMap.search([
                ("channel_id", "=", self.channel.id),
                ("external_id", "=", external_id),
            ], limit=1)
        if order_map and order_map.manomano_refused:
            return True

        contract_id = self._first_contract_id()
        if contract_id is None:
            self._log_op("refuse_order", external_id, "error",
                         "Contract ManoMano assente o non numerico sul canale.")
            return False

        body = [{
            "order_reference": external_id,
            "seller_contract_id": contract_id,
        }]
        try:
            response = self.transport.request(
                "POST", "/orders/v1/refuse-orders", json=body)
        except TransportError as exc:
            self._log_op("refuse_order", external_id, "error",
                         "Rifiuto ordine #%s fallito (rete): %s"
                         % (external_id, exc))
            return False
        if not response.ok:
            self._log_op("refuse_order", external_id, "error",
                         "Rifiuto ordine #%s: HTTP %s — %s"
                         % (external_id, response.status_code,
                            (response.text or "")[:1000]))
            return False
        if order_map:
            order_map.manomano_refused = True
        self._log_op("refuse_order", external_id, "success",
                     "Ordine #%s rifiutato su ManoMano (contract %s)."
                     % (external_id, contract_id))
        return True

    # ==================================================================
    # FATTURE — invio PDF (multipart, isolato: RestTransport invia solo JSON)
    # ==================================================================
    def push_invoice(self, invoice):
        """Carica su ManoMano il PDF della fattura confermata in Odoo.

        `POST /api/v1/orders/{order_reference}/documents?seller_contract_id=<id>`
        in **multipart/form-data** (rif. docs/manomano-api-reference.md).
        Idempotente su invoice.manomano_document_id: una fattura già inviata non
        si reinvia, così non si duplicano documenti negli archivi di ManoMano.

        Le note di credito NON si inviano: l'API documenta solo INVOICE e i
        rimborsi passano da un endpoint diverso. Si logga e si esce.
        """
        order_map = invoice.manomano_order_map_id
        if not order_map:
            return False  # non è una fattura di un ordine ManoMano
        external_id = order_map.external_id
        if invoice.manomano_document_id:
            self._log_op("push_invoice", external_id, "skip",
                         "Fattura %s già inviata a ManoMano (documento %s)."
                         % (invoice.name, invoice.manomano_document_id))
            return True
        if invoice.move_type != "out_invoice":
            self._log_op("push_invoice", external_id, "skip",
                         "%s non è una fattura cliente: ManoMano accetta solo "
                         "documenti di tipo INVOICE." % invoice.name)
            return False

        contract_id = self._first_contract_id()
        if contract_id is None:
            self._log_op("push_invoice", external_id, "error",
                         "Contract ManoMano assente o non numerico sul canale.")
            return False

        try:
            pdf, _dummy = self.env["ir.actions.report"]._render_qweb_pdf(
                "account.account_invoices", invoice.ids)
        except Exception as exc:  # noqa: BLE001 - il PDF non deve bloccare Odoo
            self._log_op("push_invoice", external_id, "error",
                         "Generazione PDF della fattura %s fallita: %s"
                         % (invoice.name, exc))
            return False

        filename = "%s.pdf" % (invoice.name or "fattura").replace("/", "-")
        document_id = self._post_invoice_document(
            external_id, filename, pdf, contract_id)
        if not document_id:
            return False
        invoice.manomano_document_id = document_id
        self._log_op("push_invoice", external_id, "success",
                     "Fattura %s inviata a ManoMano (documento %s)."
                     % (invoice.name, document_id))
        return True

    def _post_invoice_document(self, external_id, filename, pdf, contract_id):
        """Chiamata multipart vera. Ritorna il document_id, o None se fallita.

        `RestTransport` di integrations_core invia solo JSON, e integrations_core
        NON si tocca (è in produzione con BricoBravo): qui si usa `requests`
        direttamente, riusando base URL e header del trasporto già configurato.
        Duplicazione minima e consapevole, isolata in questo unico metodo.
        La api_key sta negli header e non viene MAI loggata.

        `RestTransport.request` aggiunge "Content-Type: application/json" SOLO
        agli header uniti internamente alla chiamata (`merged_headers`, locale
        al metodo): non lo scrive mai su `self.transport.default_headers`, che
        resta quello passato dal connettore (qui: solo auth + thirdparty-name).
        Per non dipendere da questo dettaglio implementativo, la copia usata
        qui rimuove comunque un'eventuale chiave Content-Type (con qualunque
        maiuscole/minuscole): con quella intestazione, `requests` non genera il
        boundary multipart e ManoMano rifiuta la richiesta.
        """
        import requests

        headers = {k: v for k, v in self.transport.default_headers.items()
                  if k.lower() != "content-type"}
        url = "%s/api/v1/orders/%s/documents" % (
            self.transport.base_url, external_id)
        try:
            response = requests.post(
                url,
                headers=headers,
                params={"seller_contract_id": contract_id},
                files={"file": (filename, pdf, "application/pdf")},
                data={"document_type": MANOMANO_DOCUMENT_TYPE_INVOICE},
                timeout=self.transport.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - rete: mai propagare
            self._log_op("push_invoice", external_id, "error",
                         "Invio fattura fallito (rete): %s" % exc)
            return None
        if not (200 <= response.status_code < 300):
            self._log_op("push_invoice", external_id, "error",
                         "Invio fattura: HTTP %s — %s"
                         % (response.status_code, (response.text or "")[:1000]))
            return None
        try:
            body = response.json()
        except ValueError:
            body = None
        document_id = self._read_document_id(body)
        if not document_id:
            self._log_op("push_invoice", external_id, "error",
                         "Invio fattura: ManoMano non ha restituito il "
                         "document_id — %s" % (response.text or "")[:1000])
        return document_id

    @staticmethod
    def _read_document_id(body):
        """document_id dalla risposta di caricamento. None se non c'è."""
        if not isinstance(body, dict):
            return None
        content = body.get("content")
        if not isinstance(content, dict):
            return None
        return content.get("document_id") or None

    def retry_pending_acquired(self):
        """Ritenta l'accettazione per gli ordini imported con acquired_done=False."""
        OrderMap = self.env["centrivo.order.map"]
        pending = OrderMap.search([
            ("channel_id", "=", self.channel.id),
            ("state", "=", "imported"),
            ("acquired_done", "=", False),
        ])
        recovered = 0
        for order_map in pending:
            try:
                if self.accept_order(order_map.external_id, order_map=order_map):
                    recovered += 1
            except Exception as exc:  # noqa: BLE001 - un ordine non blocca il batch
                _logger.warning(
                    "ManoMano retry_pending_acquired: ordine #%s fallito, "
                    "proseguo con gli altri: %s", order_map.external_id, exc)
        return recovered

    def _log_op(self, operation, external_id, result, message):
        """Scrive un record ordini su centrivo.job.log. Nessun segreto."""
        self.env["centrivo.job.log"].create({
            "channel_id": self.channel.id,
            "operation": operation,
            "external_id": external_id or False,
            "result": result,
            "message": (message or "")[:2000],
            "company_id": self.channel.company_id.id,
        })

    # ==================================================================
    # ORDINI — import (cascata prodotto, partner, sale.order, accettazione)
    # ==================================================================
    def _find_product(self, line):
        """Trova la variante prodotto in Odoo con cascata. NON crea.

        a) centrivo.sku.map (channel, external_code in [sku, ean]) — mappa umana;
        b) barcode = ean (variante o template);
        c) default_code = sku (variante o template);
        d) niente → (False, diagnostica).
        """
        Product = self.env["product.product"]
        ean = line.get("ean")
        sku = line.get("sku")
        diag = []
        codes = [c for c in (sku, ean) if c]
        if codes:
            mapped = self.env["centrivo.sku.map"].search([
                ("channel_id", "=", self.channel.id),
                ("external_code", "in", codes),
            ], limit=1)
            diag.append("sku.map(%s): %s" % (codes, "trovato" if mapped else "0"))
            if mapped:
                return mapped.product_id, ""
        if ean:
            product = Product.search(
                ["|", ("barcode", "=", ean),
                 ("product_tmpl_id.barcode", "=", ean)], limit=1)
            diag.append("barcode=%s: %s" % (ean, 1 if product else 0))
            if product:
                return product, ""
        else:
            diag.append("ean assente")
        if sku:
            product = Product.search(
                ["|", ("default_code", "=", sku),
                 ("product_tmpl_id.default_code", "=", sku)], limit=1)
            diag.append("default_code=%s: %s" % (sku, 1 if product else 0))
            if product:
                return product, ""
        else:
            diag.append("sku assente")
        return False, "; ".join(diag)

    def _find_or_create_partner(self, data):
        """Trova (dedup per vat/email) o crea res.partner dai dati normalizzati.

        Il paese si risolve da `country_iso` (dato dall'ordine, non forzato a
        IT): nessun country_iso → nessun country_id. Nessuna provincia (dato
        assente lato ManoMano ordini).
        """
        Partner = self.env["res.partner"]
        company = self.channel.company_id
        if data.get("vat"):
            found = Partner.search([("vat", "=", data["vat"])], limit=1)
            if found:
                return found
        if data.get("email"):
            found = Partner.search([("email", "=", data["email"])], limit=1)
            if found:
                return found
        vals = {
            "name": data["name"],
            "email": data.get("email") or False,
            "phone": data.get("phone") or False,
            "street": data.get("street") or False,
            "zip": data.get("zip") or False,
            "city": data.get("city") or False,
            "vat": data.get("vat") or False,
            "company_id": company.id,
        }
        country_iso = (data.get("country_iso") or "").strip()
        if country_iso:
            country = self.env["res.country"].search(
                [("code", "=", country_iso.upper())], limit=1)
            if country:
                vals["country_id"] = country.id
        return Partner.create(vals)

    def _record_order_error(self, external_id, message, sale_order=None):
        """Registra order.map=error + job.log (gemello BricoBravo, senza attività)."""
        OrderMap = self.env["centrivo.order.map"]
        company = self.channel.company_id
        existing = OrderMap.search([
            ("channel_id", "=", self.channel.id),
            ("external_id", "=", external_id),
        ], limit=1)
        vals = {
            "channel_id": self.channel.id,
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
        self._log_op("import_order", external_id, "error", message)
        _logger.warning("Import ordine ManoMano %s in errore: %s",
                        external_id, message)

    def import_order(self, external_order):
        """Traduce un ordine ManoMano in un sale.order Odoo (idempotente).

        Sequenza: 1) risoluzione prodotti (cascata; mancante → errore, nessun
        ordine parziale); 2) partner; 3) sale.order (confirm_on_import); 4)
        order.map=imported; 5) accept_order (automatico). Odoo è la fonte di
        verità: i prodotti non si creano dall'ordine.
        """
        env = self.env
        channel = self.channel
        company = channel.company_id
        external_id = self._mm_order_id(external_order)
        if not external_id:
            _logger.warning("Ordine ManoMano senza id: %s", external_order)
            return False

        OrderMap = env["centrivo.order.map"]
        existing = OrderMap.search([
            ("channel_id", "=", channel.id),
            ("external_id", "=", external_id),
        ], limit=1)
        if existing and existing.state == "imported":
            self._log_op("import_order", external_id, "skip",
                         "Ordine già importato, saltato (idempotenza).")
            return existing.sale_order_id

        # 1) PRODOTTI
        lines = self._mm_order_lines(external_order)
        order_lines = []
        missing = []
        for line in lines:
            product, diag = self._find_product(line)
            if not product:
                missing.append(diag or ("ean %s / sku %s"
                               % (line.get("ean"), line.get("sku"))))
                continue
            order_lines.append((0, 0, {
                "product_id": product.id,
                "product_uom_qty": line.get("qty") or 1,
                "price_unit": line.get("price") or 0.0,
            }))
        if missing:
            self._record_order_error(
                external_id, "Prodotto non trovato — %s" % " | ".join(missing))
            return False
        if not order_lines:
            self._record_order_error(external_id, "Ordine senza righe/prodotti.")
            return False

        # 2) PARTNER
        try:
            partner = self._find_or_create_partner(
                self._mm_partner_data(external_order))
        except Exception as exc:  # noqa: BLE001
            self._record_order_error(
                external_id, "Errore creazione cliente: %s" % exc)
            return False

        # 3) SALE ORDER (riuso se retry con sale.order già creato)
        status_label, _raw = self._mm_status(external_order)
        sale_order = existing.sale_order_id if (existing
                                                and existing.sale_order_id) else False
        if not sale_order:
            try:
                order_vals = {
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "client_order_ref": external_id,
                    "origin": external_id,
                    "note": "Stato ManoMano: %s" % status_label,
                    "order_line": order_lines,
                }
                if channel.team_id:
                    order_vals["team_id"] = channel.team_id.id
                sale_order = env["sale.order"].with_company(company).create(
                    order_vals)
            except Exception as exc:  # noqa: BLE001
                self._record_order_error(
                    external_id, "Errore creazione ordine: %s" % exc)
                return False

        # 3b) CONFERMA (se richiesto dal canale)
        if channel.confirm_on_import and sale_order.state in ("draft", "sent"):
            try:
                sale_order.action_confirm()
            except Exception as exc:  # noqa: BLE001
                self._record_order_error(
                    external_id, "Errore conferma ordine %s: %s"
                    % (sale_order.name, exc), sale_order=sale_order)
                return False

        # 4) ORDER.MAP = imported
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
        self._log_op("import_order", external_id, "success",
                     "Ordine #%s importato → %s (stato: %s)"
                     % (external_id, sale_order.name, sale_order.state))

        # 5) ACCETTAZIONE automatica
        self.accept_order(external_id, order_map=order_map)
        return sale_order

    def pull_orders(self, start_datetime=None, end_datetime=None, status=None):
        """Scarica gli ordini per ogni contract del canale e li importa.

        Ritenta prima le accettazioni pendenti. Per ogni contract, GET
        `/orders/v1/orders` con paginazione (rif. API §Ordini); per ogni
        ordine → import_order (resiliente per singolo ordine). Aggiorna
        last_pull. Ritorna il numero di ordini ricevuti.
        """
        channel = self.channel
        self.retry_pending_acquired()
        contracts = channel._manomano_contracts()
        if not contracts:
            self._log_op("pull_orders", False, "error",
                         "Nessun contract ManoMano configurato sul canale.")
            return False

        page_limit = 100
        total_received = 0
        total_imported = 0
        total_errors = 0
        for contract in contracts:
            page = 1
            pages = 1
            while page <= pages and page <= 20:  # limite di sicurezza pagine
                params = {"seller_contract_id": contract, "page": page,
                          "limit": page_limit}
                if status:
                    params["status"] = status
                if start_datetime:
                    params["created_at_start"] = start_datetime
                if end_datetime:
                    params["created_at_end"] = end_datetime
                try:
                    response = self.transport.request(
                        "GET", "/orders/v1/orders", params=params)
                except TransportError as exc:
                    self._log_op("pull_orders", False, "error",
                                 "Errore di rete (contract %s): %s"
                                 % (contract, exc))
                    break
                if not response.ok:
                    self._log_op("pull_orders", False, "error",
                                 "HTTP %s (contract %s): %s"
                                 % (response.status_code, contract,
                                    (response.text or "")[:500]))
                    break
                body = response.json if isinstance(response.json, dict) else {}
                data = body.get("content") or []
                pages = (body.get("pagination") or {}).get("pages") or 1
                total_received += len(data)
                for external_order in data:
                    try:
                        ok = self.import_order(external_order)
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        oid = self._mm_order_id(external_order)
                        if oid:
                            self._record_order_error(
                                oid, "Errore import ordine: %s" % exc)
                    if ok:
                        total_imported += 1
                    else:
                        total_errors += 1
                page += 1

        channel.last_pull = fields.Datetime.now()
        self._log_op(
            "pull_orders", False,
            "success" if total_errors == 0 else "error",
            "Ricevuti %s ordini (importati %s, errori %s), contract %s"
            % (total_received, total_imported, total_errors,
               ", ".join(contracts)))
        return total_received

    # ==================================================================
    # ORDINI — spedizione (gemello BricoBravo). Tracking URL OBBLIGATORIO.
    # ==================================================================
    def push_shipment(self, order_map):
        """Comunica a ManoMano corriere + tracking (URL obbligatorio). Idempotente.

        Precondizioni: order.map imported + sale.order; UN picking done con
        carrier_tracking_ref; carrier.map per il corriere; template URL con
        {tracking}. Endpoint reale: `POST /orders/v1/shippings`, body ARRAY con
        UN oggetto (carrier, order_reference, seller_contract_id,
        tracking_number, tracking_url, products[]) — rif.
        docs/manomano-api-reference.md §"Comunica spedizione (batch)".
        Idempotente su shipment_pushed; errori → log + False (ritentabile).
        """
        external_id = order_map.external_id
        if order_map.shipment_pushed:
            self._log_op("push_shipment", external_id, "skip",
                         "Spedizione #%s già comunicata." % external_id)
            return True
        contract_id = self._first_contract_id()
        if contract_id is None:
            self._log_op("push_shipment", external_id, "error",
                         "Contract ManoMano assente o non numerico sul canale.")
            return False
        if order_map.state != "imported" or not order_map.sale_order_id:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: ordine non importato o senza sale.order."
                         % external_id)
            return False
        sale_order = order_map.sale_order_id
        pickings = sale_order.picking_ids.filtered(
            lambda p: p.state == "done" and p.carrier_tracking_ref)
        if not pickings:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: nessuna spedizione pronta (picking done + "
                         "carrier_tracking_ref)." % external_id)
            return False
        if len(pickings) > 1:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: multi-collo non supportato (%s picking)."
                         % (external_id, len(pickings)))
            return False
        picking = pickings[0]
        source_model, source_res_id, source_display = (
            self.channel._picking_carrier_source(picking))
        if not source_res_id:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: picking %s senza vettore."
                         % (external_id, picking.name))
            return False
        carrier_map = self.env["centrivo.carrier.map"].resolve_external_code(
            self.channel, source_model, source_res_id, self.channel.company_id)
        if not carrier_map:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: nessun mapping per il vettore '%s'. "
                         "Aggiungi la riga in Integrations → Mapping Corrieri."
                         % (external_id, source_display))
            return False
        tracking_number = (picking.carrier_tracking_ref or "").strip()
        template = carrier_map.tracking_url_template or ""
        if "{tracking}" not in template:
            self._log_op("push_shipment", external_id, "error",
                         "Push #%s: template URL corriere senza {tracking} "
                         "(ManoMano esige il tracking URL)." % external_id)
            return False
        tracking_url = template.replace("{tracking}", tracking_number)

        products = []
        for line in sale_order.order_line:
            sku = (line.product_id.default_code or "").strip()
            if not sku:
                continue
            products.append({
                "seller_sku": sku,
                "quantity": line.product_uom_qty,
            })

        item = {
            "carrier": carrier_map.external_code,
            "order_reference": external_id,
            "seller_contract_id": contract_id,
            "tracking_number": tracking_number,
            "tracking_url": tracking_url,
            "products": products,
        }
        try:
            response = self.transport.request(
                "POST", "/orders/v1/shippings", json=[item])
        except TransportError as exc:
            self._log_op("push_shipment", external_id, "error",
                         "Push spedizione #%s fallito (rete): %s"
                         % (external_id, exc))
            return False
        if not response.ok:
            self._log_op("push_shipment", external_id, "error",
                         "Push spedizione #%s: HTTP %s — %s"
                         % (external_id, response.status_code,
                            (response.text or "")[:500]))
            return False
        order_map.shipment_pushed = True
        self._log_op("push_shipment", external_id, "success",
                     "Spedizione #%s comunicata a ManoMano (corriere %s, "
                     "tracking %s, %s prodotti)."
                     % (external_id, item["carrier"], tracking_number,
                        len(products)))
        return True

    # ==================================================================
    # TAXONOMY — campi del feed prodotto (SOLA LETTURA verso ManoMano)
    # ==================================================================
    def sync_taxonomy(self):
        """Scarica i campi del feed prodotto e allinea il catalogo in Odoo.

        `GET /api/v2/feeds/fields?type=product&page=&limit=` — paginato
        (rif. docs/manomano-api-reference.md). Scarta i campi riservati alla
        logistica ManoMano (contract_type 'mf'), che non riguardano chi spedisce
        da sé. Non cancella nulla: i campi spariti vengono disattivati.

        Ritorna il numero di campi pertinenti ricevuti.
        """
        entries = []
        page = 1
        pages = 1
        truncated = False
        raw_bodies = []
        while True:
            try:
                response = self.transport.request(
                    "GET", "/api/v2/feeds/fields",
                    params={"type": "product", "page": page,
                            "limit": TAXONOMY_PAGE_SIZE})
            except TransportError as exc:
                self._log_export(
                    "error",
                    "Scarico campi ManoMano fallito (rete, pagina %s): %s"
                    % (page, exc), operation="taxonomy_sync")
                return False
            if not response.ok:
                self._log_export(
                    "error",
                    "Scarico campi ManoMano: HTTP %s — %s"
                    % (response.status_code, (response.text or "")[:1000]),
                    operation="taxonomy_sync", payload=response.text or "")
                return False
            raw_bodies.append(response.text or "")
            body = response.json if isinstance(response.json, dict) else {}
            entries.extend([e for e in (body.get("content") or [])
                            if isinstance(e, dict) and is_relevant(e)])
            pagination = body.get("pagination") or {}
            pages = pagination.get("pages") or 1
            if page >= pages:
                break
            if page >= TAXONOMY_MAX_PAGES:
                # Tetto di sicurezza raggiunto con pagine ancora da leggere
                # (paginazione incoerente lato ManoMano): l'elenco è TRONCATO.
                # upsert_from_api disattiva i campi assenti dall'elenco
                # ricevuto, quindi applicare un elenco monco disattiverebbe
                # silenziosamente campi validi mai scaricati. Meglio non
                # aggiornare nulla.
                truncated = True
                break
            page += 1

        if truncated:
            self._log_export(
                "error",
                "Scarico campi ManoMano: paginazione TRONCATA al tetto di "
                "sicurezza (%s pagine lette, %s dichiarate dall'API). Elenco "
                "incompleto: NESSUNA modifica applicata al catalogo campi."
                % (TAXONOMY_MAX_PAGES, pages), operation="taxonomy_sync")
            return False

        if not entries:
            self._log_export(
                "error",
                "Scarico campi ManoMano: nessun campo pertinente ricevuto. "
                "Il feed continua a usare l'elenco di riserva.",
                operation="taxonomy_sync")
            return 0

        outcome = self.env["centrivo.manomano.feed.field"].upsert_from_api(entries)
        message = ("Campi ManoMano allineati: %s ricevuti, %s nuovi, %s "
                   "aggiornati, %s disattivati."
                   % (len(entries), outcome["created"], outcome["updated"],
                      outcome["deactivated"]))
        if outcome["anomalies"]:
            message += " Anomalie sui valori ammessi: %s" % (
                " | ".join(outcome["anomalies"][:10]))
        orfani = outcome.get("orfani") or []
        if orfani:
            message += (
                " Attenzione: %s colonne mappate non esistono nella taxonomy "
                "ManoMano attuale (probabilmente rinominate): %s. Ricontrolla "
                "le righe di mappatura che le usano."
                % (len(orfani), ", ".join(orfani[:10])))
        # I fallimenti veri (rete, HTTP, paginazione troncata, nessun campo
        # ricevuto) escono già prima con un return False. Qui si distingue fra
        # due cose diverse:
        #   - un'ANOMALIA sui valori ammessi è informativa e non richiede nulla
        #     a nessuno (es. i 7 valori di unit_count_type che ManoMano non
        #     traduce in italiano: si usano gli originali, lo scarico è
        #     riuscito) → riga verde;
        #   - una colonna mappata ORFANA richiede invece un intervento: c'è una
        #     riga di mappatura che punta a una colonna che ManoMano non
        #     conosce più, e finché non la si sistema quella cella non serve a
        #     niente → riga rossa, così si nota nel Log operazioni.
        self._log_export(
            "error" if orfani else "success",
            message, operation="taxonomy_sync",
            payload="\n".join(raw_bodies))
        return len(entries)

    # ==================================================================
    # STRATO 3 — EXPORT FEED PRODOTTO (CSV dalla taxonomy, risoluzione per MAPPATURA)
    # ==================================================================
    def generate_product_feed(self):
        """Genera il CSV feed prodotto ManoMano e lo salva sul canale.

        Odoo fonte di verità (sola lettura). Selezione per tag; header dalla
        taxonomy scaricata (riserva cablata se non ancora scaricata, vedi
        `_feed_columns`); ogni cella risolta dalla MAPPATURA del canale
        (centrivo.manomano.feed.map). Colonne non mappate → vuote. Avviso (non
        blocco) se manca una colonna obbligatoria o se una colonna obbligatoria
        mappata risulta vuota. Elaborazione a blocchi. Aggiorna sempre
        `product_feed_warnings_count` (letto dall'azione del canale per
        decidere il colore della notifica).
        """
        from odoo.addons.integrations_core.connectors.transport import CsvSerializer
        channel = self.channel
        company = channel.company_id
        columns = self._feed_columns()
        tags = channel.export_product_tag_ids
        if not tags:
            self._log_export("skip",
                             "Nessun tag prodotti: feed prodotto ManoMano VUOTO.",
                             operation="manomano_product_feed")
            self._save_product_feed(CsvSerializer().serialize(columns, []))
            self.product_feed_warnings_count = 0
            return 0

        index = self._feed_map_index(channel)  # {nome colonna: mapping record}
        self._warn_missing_mandatory(channel, index)

        # Cache dei valori ammessi per colonna (vedi _check_cell): azzerata a
        # ogni generazione, così un successivo scarico di taxonomy non la
        # lascia stantia.
        self._check_cell_ammessi_cache = {}

        Product = self.env["product.product"].with_company(company)
        products = Product.search(
            [("product_tmpl_id.product_tag_ids", "in", tags.ids)], order="id")
        total = len(products)
        rows = []
        warnings = []
        warnings_count = 0
        mandatory_empty_counts = {}
        BATCH = 500
        for offset in range(0, total, BATCH):
            batch = products[offset:offset + BATCH]
            main_img_ids, gallery_img_ids = self._image_attachment_sets(batch)
            for product in batch:
                row = []
                for col in columns:
                    mapping = index.get(col)
                    cell = self._resolve_feed_cell(
                        product, mapping, main_img_ids, gallery_img_ids)
                    warning = self._check_cell(mapping, cell)
                    if warning:
                        warnings_count += 1
                        if len(warnings) < 200:
                            warnings.append("%s — %s" % (
                                product.default_code or product.display_name,
                                warning))
                    if self._is_mandatory_empty(mapping, cell):
                        mandatory_empty_counts[col] = (
                            mandatory_empty_counts.get(col, 0) + 1)
                    row.append(cell)
                rows.append(row)
            self.env.invalidate_all()

        content = CsvSerializer().serialize(columns, rows)
        self._save_product_feed(content)

        if mandatory_empty_counts:
            total_mandatory_empty = sum(mandatory_empty_counts.values())
            warnings_count += total_mandatory_empty
            colonne = ", ".join(
                "%s (%s)" % (col, n)
                for col, n in sorted(mandatory_empty_counts.items()))
            self._log_export(
                "error",
                "Feed prodotto: %s celle obbligatorie mappate ma VUOTE, sulle "
                "colonne: %s. ManoMano potrebbe rifiutare o scartare quelle "
                "schede in silenzio." % (total_mandatory_empty, colonne),
                operation="manomano_product_feed")

        if warnings_count:
            count_text = ("almeno %s" % warnings_count
                          if warnings_count > 200 else str(warnings_count))
            self._log_export(
                "error",
                "Feed prodotto: %s valori da rivedere (il feed è stato generato "
                "lo stesso). Primi casi: %s"
                % (count_text, " | ".join(warnings[:50])),
                operation="manomano_product_feed")
        self._log_export(
            "success", "Feed prodotto ManoMano: %s righe." % len(rows),
            operation="manomano_product_feed")
        self.product_feed_warnings_count = warnings_count
        return len(rows)

    def _feed_map_index(self, channel):
        """Dizionario {nome colonna ManoMano: mapping record} per accesso O(1)."""
        return {m.mm_field_id.name: m for m in channel.manomano_feed_map_ids
                if m.mm_field_id}

    def _feed_columns(self):
        """Intestazioni del CSV: dalla taxonomy SCARICATA, altrimenti riserva.

        "Taxonomy disponibile" vuol dire campi REALMENTE scaricati da ManoMano,
        cioè con `mm_id` valorizzato: i segnaposto creati dalla migrazione (per
        le colonne già in uso prima di questo aggiornamento) hanno `mm_id`
        vuoto finché non si preme «Aggiorna campi da ManoMano», e NON contano
        come taxonomy disponibile. Senza questa distinzione, generare il feed
        subito dopo l'aggiornamento del modulo (prima del primo scarico)
        produrrebbe un'intestazione con solo le poche colonne segnaposto invece
        delle colonne ufficiali. L'elenco cablato MANOMANO_FEED_COLUMNS resta
        come RISERVA per il primo avvio, quando la taxonomy non è ancora stata
        scaricata per niente.
        """
        fields_model = self.env["centrivo.manomano.feed.field"]
        records = fields_model.search(
            [("contract_type", "!=", "mf"), ("mm_id", "not in", (False, ""))])
        names = [r.name for r in records if r.name]
        return names or list(MANOMANO_FEED_COLUMNS)

    def _check_cell(self, mapping, value):
        """Avviso sulla singola cella, o None se va bene. Non blocca mai.

        Controlla che il valore sia tra quelli ammessi (anche nel caso multi-valore
        separato da '#') e che un campo numerico contenga un numero. La cella vuota
        non è un errore qui: gli obbligatori si controllano a parte, sulla
        mappatura.

        L'insieme dei valori ammessi (`ammessi`) è calcolato una SOLA VOLTA per
        colonna (chiave: id del campo taxonomy) e riusato per tutte le celle:
        su un catalogo di migliaia di prodotti con più colonne a lista chiusa,
        ricostruirlo per ogni cella sarebbe lavoro ripetuto nel punto più
        caldo della generazione del feed. La cache vive sull'attributo
        d'istanza `_check_cell_ammessi_cache`, azzerato a ogni chiamata di
        `generate_product_feed` (vedi lì): non sopravvive quindi a una
        generazione successiva, che potrebbe partire da una taxonomy appena
        aggiornata da `sync_taxonomy`.
        """
        if not mapping or not mapping.mm_field_id or not value:
            return None
        field = mapping.mm_field_id
        if field.has_values:
            cache = self.__dict__.setdefault("_check_cell_ammessi_cache", {})
            ammessi = cache.get(field.id)
            if ammessi is None:
                ammessi = {v.name for v in field.value_ids}
                cache[field.id] = ammessi
            fuori = [part for part in str(value).split("#")
                     if part and part not in ammessi]
            if fuori:
                return ("colonna %s: valore non ammesso da ManoMano: %s"
                        % (field.name, ", ".join(fuori)))
        if field.datatype == "numerical":
            try:
                float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                return ("colonna %s: atteso un valore numerico, trovato «%s»"
                        % (field.name, value))
        return None

    @staticmethod
    def _is_mandatory_empty(mapping, value):
        """True se la colonna è mappata su un campo obbligatorio ma la cella è vuota.

        `_check_cell` esce subito sulle celle vuote (non è compito suo), quindi
        una colonna obbligatoria MAPPATA ma vuota (es. `description` puntata su
        un campo Odoo vuoto per migliaia di prodotti) passerebbe inosservata:
        ManoMano scarterebbe quelle schede in silenzio. Questo controllo colma
        il vuoto, senza toccare _check_cell.
        """
        return bool(mapping and mapping.mm_field_id
                    and mapping.mm_field_id.mandatory and not value)

    def _resolve_feed_cell(self, product, mapping, main_img_ids, gallery_img_ids):
        """Valore di UNA cella secondo la mappatura. '' se non mappata."""
        if not mapping:
            return ""
        st = mapping.source_type
        if st == "fixed":
            return mapping.fixed_value or ""
        if st == "field":
            return self._mapped_value(product, mapping.source_field_id)
        if st == "main_image":
            urls = self._image_urls(product, self.channel,
                                    main_img_ids, gallery_img_ids)
            return urls[0] if urls else ""
        if st == "gallery_image":
            urls = self._image_urls(product, self.channel,
                                    main_img_ids, gallery_img_ids)
            idx = mapping.image_index or 1
            # urls[0] è la principale; la galleria parte da urls[1]
            pos = idx  # 1→urls[1], 2→urls[2], ...
            return urls[pos] if pos < len(urls) else ""
        if st == "attribute":
            return self._attribute_value(product, mapping.attribute_id)
        return ""

    @staticmethod
    def _attribute_value(product, attribute):
        """Valori di un attributo sul prodotto, uniti col separatore ManoMano.

        Gli attributi che NON generano varianti vivono sulle righe attributo del
        TEMPLATE (`attribute_line_ids`), non sulla variante: si legge da lì, così
        funziona sia per i prodotti con varianti sia per quelli senza.
        ManoMano vuole i valori multipli separati da '#'
        (rif. docs/manomano-api-reference.md §Taxonomy).
        """
        if not attribute:
            return ""
        template = getattr(product, "product_tmpl_id", None)
        if not template:
            return ""
        names = []
        for line in template.attribute_line_ids:
            if line.attribute_id.id != attribute.id:
                continue
            for value in line.value_ids:
                if value.name and value.name not in names:
                    names.append(value.name)
        return "#".join(names)

    def _warn_missing_mandatory(self, channel, index):
        """Avviso (non blocca) sulle colonne obbligatorie non mappate.

        L'obbligatorietà viene dalla taxonomy scaricata; in sua assenza si usa
        l'elenco cablato di riserva. L'EAN è surrogabile da sku_manufacturer +
        brand (regola dichiarata da ManoMano nella descrizione del campo): può
        mancare SOLO se sono mappati ENTRAMBI, non basta uno dei due.
        """
        records = self.env["centrivo.manomano.feed.field"].search(
            [("mandatory", "=", True), ("contract_type", "!=", "mf")])
        obbligatori = {r.name for r in records} or set(MANOMANO_FEED_MANDATORY)
        surrogabili = {"ean", "sku_manufacturer"}
        missing = [c for c in obbligatori - surrogabili if c not in index]
        if "ean" not in index and not ({"sku_manufacturer", "brand"}
                                       <= set(index)):
            missing.append("ean (oppure sku_manufacturer + brand)")
        if missing:
            self._log_export(
                "error",
                "Feed prodotto: colonne obbligatorie NON mappate: %s. Il feed "
                "verrà comunque generato ma ManoMano potrebbe rifiutare le schede."
                % ", ".join(sorted(missing)),
                operation="manomano_product_feed")

    def _save_product_feed(self, content):
        self.channel.write({
            "manomano_product_feed_content": content,
            "manomano_product_feed_generated_at": fields.Datetime.now(),
        })

    def _log_export(self, result, message, operation, payload=None):
        """Scrive un record su centrivo.job.log.

        `payload`, se valorizzato, finisce (troncato a PAYLOAD_LOG_LIMIT) nel
        campo omonimo del log: serve alla diagnosi, come già fanno
        push_offers/check_offers tramite `_log_offers`. Opzionale apposta: le
        chiamate esistenti che non lo passano restano invariate.
        """
        vals = {
            "channel_id": self.channel.id,
            "operation": operation,
            "result": result,
            "message": message[:2000],
            "company_id": self.channel.company_id.id,
        }
        if payload:
            vals["payload"] = payload[:PAYLOAD_LOG_LIMIT]
        self.env["centrivo.job.log"].create(vals)

    # ------------------------------------------------------------------
    # Helper immagini + campo mappato — PORTATI (copiati, non importati) da
    # marketplace_bricobravo/connectors/bricobravo.py per isolamento tra
    # connettori: ogni marketplace resta indipendente.
    # ------------------------------------------------------------------
    def _image_attachment_sets(self, products):
        """Insiemi degli id CON immagine, risolti via ir.attachment (NIENTE byte).

        I campi immagine (`image_1920`) sono memorizzati come ir.attachment
        (attachment=True): sapere QUALI prodotti/immagini hanno un blob si ottiene
        con una sola query sugli allegati (res_model/res_field/res_id), senza MAI
        caricare i byte in memoria. Leggere `image_1920` in un loop, invece,
        forzerebbe il prefetch dei BLOB a piena risoluzione sull'intero recordset
        → memoria saturata e worker ucciso (rischio noto, vedi BricoBravo TASK_85).

        Ritorna (main_img_ids, gallery_img_ids):
          - main_img_ids: id dei product.product con immagine principale, sia
            propria (attachment su product.product) sia EREDITATA dal template
            (attachment su product.template): in Odoo i prodotti senza varianti
            tengono l'immagine sul template e la variante la eredita (image_1920
            related). Senza considerare il template, l'URL immagine non veniva
            mai generato per quei prodotti (colonna image_1 vuota nel feed);
          - gallery_img_ids: id dei product.image (galleria) con image_1920.
        """
        Attachment = self.env["ir.attachment"].sudo()
        product_ids = products.ids
        main_img_ids = set()
        if product_ids:
            # a) immagine sulla VARIANTE (product.product).
            main_img_ids = set(Attachment.search([
                ("res_model", "=", "product.product"),
                ("res_field", "=", "image_1920"),
                ("res_id", "in", product_ids),
            ]).mapped("res_id"))
            # b) immagine sul TEMPLATE: la variante la eredita. Mappiamo
            # template → varianti e aggiungiamo le varianti il cui template ha
            # l'immagine (la rotta immagine serve product.product.image_1920,
            # che è related e restituisce quella del template).
            tmpl_to_products = {}
            for product in products:
                tmpl_to_products.setdefault(
                    product.product_tmpl_id.id, []).append(product.id)
            tmpl_with_img = set(Attachment.search([
                ("res_model", "=", "product.template"),
                ("res_field", "=", "image_1920"),
                ("res_id", "in", list(tmpl_to_products.keys())),
            ]).mapped("res_id"))
            for tmpl_id in tmpl_with_img:
                main_img_ids.update(tmpl_to_products.get(tmpl_id, []))

        gallery_img_ids = set()
        if "product.image" in self.env:
            # Solo gli id delle immagini di galleria (relazione leggera, niente
            # byte); poi una query allegati per sapere quali hanno il blob.
            image_ids = products.mapped(
                "product_tmpl_id.product_template_image_ids").ids
            if image_ids:
                gallery_img_ids = set(Attachment.search([
                    ("res_model", "=", "product.image"),
                    ("res_field", "=", "image_1920"),
                    ("res_id", "in", image_ids),
                ]).mapped("res_id"))
        return main_img_ids, gallery_img_ids

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

    def _image_urls(self, product, channel, main_img_ids, gallery_img_ids):
        """COSTRUISCE gli URL immagine verso la rotta pubblica del controller (max 10).

        Le immagini NON sono servite dal /web/image nativo (su Community puro il
        pubblico riceve il placeholder): vanno alla rotta dedicata del controller
        di integrations_core (già esistente, riusata invariata), che legge i byte
        in sudo e li serve solo per i prodotti esportabili. Gli URL portano lo
        STESSO export_token del feed (se Angelo lo rigenera, cambiano sia gli URL
        feed sia quelli immagine — coerente).

        L'ESISTENZA delle immagini è passata come id-set (main_img_ids /
        gallery_img_ids, vedi _image_attachment_sets): NON si accede mai a
        `image_1920`, che caricherebbe i byte a piena risoluzione dell'intero
        recordset via prefetch.

          - urls[0] (principale) = se il prodotto ha image_1920:
            {base_url}/integrations/feed/image/<channel_id>/product/<product_id>?token=<export_token>
          - urls[1..] (galleria) = per ogni product.image (accesso DINAMICO,
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
        if product.id in main_img_ids:
            urls.append("%s/product/%s?token=%s" % (prefix, product.id, token))

        # 2..10) Galleria product.image (accesso dinamico, opzionale).
        if "product.image" in self.env:
            gallery = getattr(
                product.product_tmpl_id, "product_template_image_ids", False)
            if gallery:
                for image in gallery.sorted("sequence"):
                    if image.id not in gallery_img_ids:
                        continue
                    urls.append("%s/gallery/%s?token=%s" % (prefix, image.id, token))
                    if len(urls) >= 10:
                        break
        elif not getattr(self, "_image_model_warned", False):
            _logger.info(
                "product.image non presente: nel feed prodotto solo l'immagine "
                "principale.")
            self._image_model_warned = True

        return urls[:10]
