# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sync.channel — configurazione di sync di un fornitore dropship.

Un record per fornitore: mappatura colonne SALVATA (column_map_ids, mappatura
dinamica) e — per il solo Flusso B (task successivo) — i parametri SFTP. Il
FLUSSO A (caricamento prodotti) parte da un WIZARD con file caricato a mano (vedi
centrivo.sync.import.wizard) che ACCODA un centrivo.sync.import.job; qui vivono i
metodi d'import riusabili a CHUNK (_import_rows = FASE 1; _download_images =
FASE 2) e i loro helper (match, supplierinfo/supply). L'esecuzione è ASINCRONA in
background (cron), senza limite di tempo della request web.

NB (v2.2): il modulo NON genera più il riferimento interno (default_code). È una
COLONNA del file (destinazione speciale "Riferimento interno"), compilata a mano
nell'Excel. Rimossi: campo next_hdc_number e generatore HDC anti-collisione.

Disciplina: scrittura via ORM (mai SQL), company_id ovunque, segreti (password
SFTP) MAI nei log. Riusa SftpTransport (Flusso B) e centrivo.job.log.
"""
import base64
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..connectors import catalog_parser as cp

_logger = logging.getLogger(__name__)

# Ogni quante righe loggare il progresso in FASE 1 (visibilità nel Log operazioni).
_COMMIT_BATCH = 50

# Flusso B (giacenze): righe di file elaborate per singolo tick di cron (ripresa da
# cursore, come l'import prodotti). Tenuto sotto qualunque limite di tempo del cron
# worker anche su file grandi (es. CARFER ~25k righe). L'apply delle rettifiche è in
# BULK per blocco (una sola action_apply_inventory), non riga per riga.
_STOCK_ROW_CHUNK = 2000
# Ogni quante ore un canale "attivo" (ricorrente) è ri-sincronizzato dal cron.
_STOCK_RECUR_HOURS = 6

# FASE 2 immagini — header HTTP "da browser": alcune CDN (cloudhub/Akamai)
# rispondono 403 ai client non-browser (cfr. TASK_44 per GLS). Non aggira alcuna
# autenticazione: imita solo un browser. Timeout (connect, read) corto per non
# far sforare il tempo del chunk; un URL fallito non blocca (resilienza FASE 2).
# Con blocchi da 6 immagini (vedi sync_import_job._IMAGE_CHUNK) il caso peggiore
# 6*(5+8)=78s resta ben sotto i 120s del cron worker anche dove
# limit_time_real_cron non fosse applicato.
_IMAGE_TIMEOUT = (5, 8)
_IMAGE_HTTP_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

# Campi dimensione candidati (Odoo Community puro NON ne ha di nativi: presenti
# solo con moduli tipo product_dimension OCA). Letti in modo DINAMICO/OPZIONALE.
_DIMENSION_CANDIDATES = {
    "lunghezza": ("product_length", "length", "x_length"),
    "larghezza": ("product_width", "width", "x_width"),
    "altezza": ("product_height", "height", "x_height"),
}

# Mappa destinazione speciale dimensione → chiave dimensione interna.
_DIMENSION_SPECIAL = {
    "dimension_l": "lunghezza",
    "dimension_w": "larghezza",
    "dimension_h": "altezza",
}

# Mappa destinazione speciale categoria → campo di appoggio su product.template.
_CATEGORY_SPECIAL = {
    "supplier_cat_1": "dropship_macro",
    "supplier_cat_2": "dropship_categoria",
    "supplier_cat_3": "dropship_gerarchia",
}


class CentrivoSyncChannel(models.Model):
    _name = "centrivo.sync.channel"
    _description = "Canale di sync fornitore dropship"

    name = fields.Char(string="Nome", required=True)
    active = fields.Boolean(string="Attivo", default=True)

    dropship_supplier_id = fields.Many2one(
        "res.partner", string="Fornitore dropship", required=True,
        help="Fornitore (creato a mano). Il modulo NON crea il partner fornitore.")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company,
        help="Azienda dei prodotti/righe create (default HD CASA).")

    # --- Mappatura colonne SALVATA per il fornitore (mappatura dinamica). ---
    column_map_ids = fields.One2many(
        "centrivo.sync.column.map", "channel_id", string="Mappatura colonne",
        help="Mappatura colonna→destinazione salvata per questo fornitore; il "
             "wizard di import la ri-propone ai caricamenti successivi.")

    last_catalog_import = fields.Datetime(
        string="Ultimo import prodotti", readonly=True)
    supply_count = fields.Integer(
        string="Righe disponibilità", compute="_compute_supply_count")

    # --- Import in background (asincrono): storico job + stato a colpo d'occhio.
    import_job_ids = fields.One2many(
        "centrivo.sync.import.job", "channel_id", string="Job import")
    last_import_state = fields.Selection(
        selection=[
            ("queued", "In coda"), ("running", "In corso"),
            ("done", "Completato"), ("error", "Con errori"),
            ("cancelled", "Annullato")],
        string="Stato ultimo import", compute="_compute_last_import")
    last_import_message = fields.Char(
        string="Esito ultimo import", compute="_compute_last_import")

    # --- Connessione al server del fornitore — SOLO Flusso B (giacenze). La
    #     password è SEGRETA: mai nei log. SftpTransport/FtpTransport in
    #     integrations_core. I campi restano nominati sftp_* per compatibilità DB
    #     ma servono tutti i protocolli (SFTP/FTP/FTPS). ---
    transfer_protocol = fields.Selection(
        selection=[
            ("sftp", "SFTP (SSH)"),
            ("ftp", "FTP"),
            ("ftps", "FTPS (FTP esplicito su TLS)")],
        string="Protocollo", default="sftp", required=True,
        help="Protocollo del server giacenze del fornitore. SFTP = SSH (porta 22 "
             "tipica). FTP = FTP classico (porta 21; credenziali IN CHIARO). "
             "FTPS = FTP esplicito su TLS (porta 21, cifrato: preferibile all'FTP "
             "se il fornitore lo supporta).")
    sftp_host = fields.Char(string="Host")
    sftp_port = fields.Integer(string="Porta", default=22)
    sftp_username = fields.Char(string="Utente")
    sftp_password = fields.Char(string="Password")
    path_giacenze = fields.Char(
        string="File giacenze (csv)",
        help="Nome del file giacenze sull'SFTP (es. GIACENZE.csv). Usato dal "
             "Flusso B (aggiornamento disponibilità).")
    stock_feed_active = fields.Boolean(
        string="Feed giacenze attivo", default=False,
        help="INTERRUTTORE on/off PER-FORNITORE del sync ricorrente delle "
             "giacenze (Flusso B): se attivo, il cron aggiorna periodicamente la "
             "disponibilità di questo fornitore. Il bottone 'Aggiorna giacenze "
             "ora' funziona comunque, anche a flag spento.")

    # --- Flusso B: dove e come scrivere la disponibilità del fornitore. ---
    stock_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Magazzino giacenze",
        help="Magazzino DEDICATO di questo fornitore dropship: la disponibilità "
             "letta dal file giacenze viene scritta come stock Odoo nativo nella "
             "sua giacenza-ubicazione. In valorizzazione si esclude questo "
             "magazzino (merce non di proprietà).")
    stock_code_header = fields.Char(
        string="Colonna codice (giacenze)",
        help="Intestazione REALE della colonna CODICE nel file giacenze (chiave "
             "di match, es. 'Codice'). Mappatura dinamica salvata per fornitore.")
    stock_qty_header = fields.Char(
        string="Colonna disponibilità (giacenze)",
        help="Intestazione REALE della colonna DISPONIBILITÀ nel file giacenze "
             "(es. 'Disponibilita'). La colonna prezzo, se presente, è IGNORATA.")
    last_stock_sync = fields.Datetime(
        string="Ultimo aggiornamento giacenze", readonly=True)
    stock_feed_pending = fields.Boolean(
        string="Aggiornamento giacenze in coda", readonly=True, copy=False,
        help="True quando un 'Aggiorna giacenze ora' è stato accodato: il cron "
             "esecutore lo elabora al primo tick e azzera il flag.")

    # --- Run-state del feed giacenze a CHUNK (ripresa da cursore, come Flusso A).
    #     Il file grande (es. CARFER ~25k righe) viene elaborato a blocchi su più
    #     tick di cron: nessun singolo tick sfora il limite di tempo del worker. ---
    stock_feed_state = fields.Selection(
        selection=[("idle", "Fermo"), ("running", "In corso")],
        string="Stato feed giacenze", default="idle", readonly=True, copy=False)
    stock_feed_blob = fields.Binary(
        string="Snapshot file giacenze", attachment=True, copy=False,
        help="Copia del file scaricata all'avvio del run: garantisce coerenza tra "
             "i blocchi anche se il fornitore aggiorna il file durante l'elaborazione.")
    stock_feed_cursor = fields.Integer(
        string="Righe giacenze elaborate", default=0, readonly=True, copy=False)
    stock_feed_total = fields.Integer(
        string="Righe giacenze totali", default=0, readonly=True, copy=False)
    stock_feed_matched_json = fields.Text(
        string="ID prodotti abbinati (JSON)", copy=False,
        help="ID dei prodotti aggiornati nel run in corso: servono per azzerare a "
             "fine run i codici spariti dal file.")
    stock_feed_updated_count = fields.Integer(
        string="Giacenze aggiornate (run)", default=0, readonly=True, copy=False)
    stock_feed_notfound_count = fields.Integer(
        string="Codici non trovati (run)", default=0, readonly=True, copy=False,
        help="Codici del file non presenti a catalogo (anti-fantasma): conteggiati, "
             "non più loggati riga per riga (erano decine di migliaia di scritture).")

    # ------------------------------------------------------------------
    # Helper (riusati da TASK_78)
    # ------------------------------------------------------------------
    def _compute_supply_count(self):
        Supply = self.env["centrivo.sync.supply"]
        for channel in self:
            channel.supply_count = Supply.search_count(
                [("channel_id", "=", channel.id)])

    @api.depends("import_job_ids.state", "import_job_ids.message")
    def _compute_last_import(self):
        Job = self.env["centrivo.sync.import.job"]
        for channel in self:
            job = Job.search(
                [("channel_id", "=", channel.id)],
                order="create_date desc", limit=1)
            channel.last_import_state = job.state if job else False
            channel.last_import_message = job.message if job else False

    @api.onchange("transfer_protocol")
    def _onchange_transfer_protocol(self):
        """Suggerisce la porta di default del protocollo (22 SFTP / 21 FTP·FTPS).

        Aggiorna solo se la porta è vuota o è ancora quella di default dell'altro
        protocollo, per non sovrascrivere una porta custom impostata a mano.
        """
        defaults = {"sftp": 22, "ftp": 21, "ftps": 21}
        target = defaults.get(self.transfer_protocol)
        if target and self.sftp_port in (False, 0, 21, 22):
            self.sftp_port = target

    def _build_transport(self):
        """Costruisce il trasporto giusto (SFTP/FTP/FTPS) dal protocollo del canale."""
        self.ensure_one()
        from odoo.addons.integrations_core.connectors.transport import (
            SftpTransport, FtpTransport)
        proto = self.transfer_protocol or "sftp"
        if proto == "sftp":
            return SftpTransport(
                host=self.sftp_host, username=self.sftp_username,
                password=self.sftp_password, port=self.sftp_port or 22)
        return FtpTransport(
            host=self.sftp_host, username=self.sftp_username,
            password=self.sftp_password, port=self.sftp_port or 21,
            use_tls=(proto == "ftps"))

    def _log(self, operation, result, message, payload=None):
        """Scrive una riga su centrivo.job.log (segreti MAI loggati)."""
        self.ensure_one()
        self.env["centrivo.job.log"].sudo().create({
            "operation": operation,
            "result": result,
            "message": "[%s] %s" % (self.name, message),
            "payload": (payload or "")[:2000] or False,
            "company_id": self.company_id.id,
        })

    def _check_default_code_duplicate(self, ref_value, code, matched_product,
                                      seen_refs):
        """Controllo DIFENSIVO (non bloccante) sui duplicati di default_code (§9.1).

        Logga un AVVISO se lo stesso riferimento interno compare nel file su codici
        fornitore diversi, o se esiste già su un prodotto DIVERSO in Odoo. Non
        interrompe l'import, non deduplica, non rinomina.
        """
        self.ensure_one()
        prior_code = seen_refs.get(ref_value)
        if prior_code is None:
            seen_refs[ref_value] = code
        elif prior_code != code:
            self._log("sync_import", "success",
                      "AVVISO possibile duplicato: riferimento interno '%s' presente "
                      "nel file su codici fornitore diversi (%s e %s)."
                      % (ref_value, prior_code, code))
        Template = self.env["product.template"].with_context(active_test=False)
        matched_tmpl = matched_product.product_tmpl_id if matched_product else False
        others = Template.search([("default_code", "=", ref_value)]).filtered(
            lambda t: t != matched_tmpl)
        if others:
            self._log("sync_import", "success",
                      "AVVISO possibile duplicato: riferimento interno '%s' già "
                      "presente su %s prodotto/i diverso/i in Odoo (riga codice %s)."
                      % (ref_value, len(others), code))

    def _long_desc_field(self):
        """Campo destinazione della descrizione lunga (description_sale → fallback)."""
        fields_map = self.env["product.template"]._fields
        return "description_sale" if "description_sale" in fields_map else "description"

    def _dimension_fields(self):
        """Mappa {dim: campo_odoo} dei campi dimensione presenti (o {} se assenti)."""
        fields_map = self.env["product.template"]._fields
        resolved = {}
        for dim, candidates in _DIMENSION_CANDIDATES.items():
            for candidate in candidates:
                if candidate in fields_map:
                    resolved[dim] = candidate
                    break
        return resolved

    def _upsert_supplierinfo(self, template, codice, price=None, product_name=None):
        """Crea o aggiorna product.supplierinfo NATIVO per (fornitore, template).

        `price=None` → NON tocca il prezzo (giro senza colonna costo attiva).
        """
        SupplierInfo = self.env["product.supplierinfo"]
        existing = SupplierInfo.search([
            ("partner_id", "=", self.dropship_supplier_id.id),
            ("product_tmpl_id", "=", template.id),
        ], limit=1)
        vals = {"product_code": codice}
        if price is not None:
            vals["price"] = price
        if product_name:
            vals["product_name"] = product_name
        if existing:
            existing.write(vals)
        else:
            vals.update({
                "partner_id": self.dropship_supplier_id.id,
                "product_tmpl_id": template.id,
            })
            SupplierInfo.create(vals)

    def _upsert_supply(self, product, codice):
        """Crea o aggiorna centrivo.sync.supply (qty gestita dal Flusso B)."""
        Supply = self.env["centrivo.sync.supply"]
        existing = Supply.search([
            ("product_id", "=", product.id),
            ("dropship_supplier_id", "=", self.dropship_supplier_id.id),
            ("company_id", "=", self.company_id.id),
        ], limit=1)
        if existing:
            existing.write({"supplier_sku": codice, "channel_id": self.id})
        else:
            Supply.create({
                "product_id": product.id,
                "dropship_supplier_id": self.dropship_supplier_id.id,
                "channel_id": self.id,
                "supplier_sku": codice,
                "qty_available": 0.0,
                "company_id": self.company_id.id,
            })

    def _find_existing_product(self, codice, barcode):
        """Cerca un prodotto esistente per supplier_sku (nel fornitore) o barcode."""
        SupplierInfo = self.env["product.supplierinfo"]
        Product = self.env["product.product"]
        if codice:
            seller = SupplierInfo.search([
                ("partner_id", "=", self.dropship_supplier_id.id),
                ("product_code", "=", codice),
            ], limit=1)
            if seller and seller.product_tmpl_id:
                return seller.product_tmpl_id.product_variant_id
        if barcode:
            product = Product.search([("barcode", "=", barcode)], limit=1)
            if product:
                return product
        return Product.browse()

    # ------------------------------------------------------------------
    # FLUSSO A — apertura wizard
    # ------------------------------------------------------------------
    def action_open_import_wizard(self):
        """Bottone: apre il wizard di import prodotti (mappatura dinamica)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Importa prodotti"),
            "res_model": "centrivo.sync.import.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_channel_id": self.id},
        }

    # ------------------------------------------------------------------
    # FLUSSO A — ENGINE d'import (mista crea+aggiorna, applicazione selettiva)
    #
    # L'esecuzione è ASINCRONA in background: il wizard accoda un
    # centrivo.sync.import.job che richiama _import_rows / _download_images a
    # CHUNK da un cron (nessun limite di tempo della request). Questi metodi NON
    # committano: il commit del cursore è del job (vedi sync_import_job.py).
    # ------------------------------------------------------------------
    def _key_header(self, specs):
        """Intestazione della colonna-CHIAVE (codice fornitore) tra le attive.

        Solleva se assente: è la chiave di abbinamento, indispensabile.
        """
        key_header = next(
            (s["header"] for s in specs
             if s["kind"] == "special" and s["special"] == "supplier_code"), None)
        if not key_header:
            raise UserError(_(
                "Mappa e attiva la colonna 'Codice fornitore (CHIAVE)': è la "
                "chiave di abbinamento, indispensabile per l'import."))
        return key_header

    def _import_rows(self, rows, specs, start_index=0):
        """FASE 1 (per-CHUNK): importa la slice `rows` con le colonne attive.

        `specs`: lista di dict {header, kind('special'/'field'), special,
        field_name} delle SOLE colonne attive (kind != ignore). `start_index`:
        offset assoluto della prima riga della slice (numerazione log coerente
        sull'intero file). NON committa (lo fa il job chiamante). Ritorna
        (stats_dict, image_jobs) con image_jobs = [(product_id, url)].
        """
        self.ensure_one()
        key_header = self._key_header(specs)
        # Per CREARE servono, tra le colonne attive, Nome + Riferimento interno
        # (+ Codice fornitore, già garantito dalla chiave). Vedi spec §9.
        has_name = any(
            s["kind"] == "special" and s["special"] == "name" for s in specs)
        has_ref = any(
            s["kind"] == "special" and s["special"] == "internal_reference"
            for s in specs)
        can_create = has_name and has_ref

        created = updated = skipped = errors = 0
        image_jobs = []  # [(product_id, url)]
        # Controllo difensivo duplicati default_code (§9.1): all'interno del
        # blocco via seen_refs; tra blocchi diversi il controllo DB (prodotti già
        # committati) lo copre comunque.
        seen_refs = {}
        for offset, row in enumerate(rows):
            index = start_index + offset + 1
            try:
                code = cp.normalize_code(row.get(key_header))
                if not code:
                    skipped += 1
                    self._log("sync_import", "skip",
                              "Riga %s: codice fornitore mancante." % index)
                    continue
                prod_vals, barcode, cost, image_url, name_value, ref_value = \
                    self._collect_row(specs, row)
                existing = self._find_existing_product(code, barcode)
                if ref_value:
                    self._check_default_code_duplicate(
                        ref_value, code, existing, seen_refs)
                if existing:
                    template = existing.product_tmpl_id
                    if prod_vals:
                        template.write(prod_vals)
                    self._upsert_supplierinfo(
                        template, code, price=cost, product_name=name_value or None)
                    self._upsert_supply(existing, code)
                    if image_url and not existing.image_1920:
                        image_jobs.append((existing.id, image_url))
                    updated += 1
                elif can_create and name_value and ref_value:
                    product = self._create_product(code, prod_vals, name_value)
                    self._upsert_supplierinfo(
                        product.product_tmpl_id, code, price=cost,
                        product_name=name_value)
                    self._upsert_supply(product, code)
                    if image_url:
                        image_jobs.append((product.id, image_url))
                    created += 1
                else:
                    skipped += 1
                    self._log("sync_import", "skip",
                              "SKU %s sconosciuto: creazione non possibile (servono "
                              "Nome + Riferimento interno + Codice fornitore "
                              "mappati/attivi e valorizzati)." % code)
            except Exception as exc:  # noqa: BLE001 — una riga non blocca le altre
                errors += 1
                self._log("sync_import", "error", "Riga %s: %s" % (index, exc))
                _logger.exception("Errore import riga %s", index)
            if index % _COMMIT_BATCH == 0:
                self._log("sync_import", "success",
                          "Progresso riga %s: creati %s, aggiornati %s, saltati %s, "
                          "errori %s (blocco)." % (index, created, updated, skipped,
                                                   errors))
        return ({"created": created, "updated": updated, "skipped": skipped,
                 "errors": errors}, image_jobs)

    def _collect_row(self, specs, row):
        """Costruisce i vals prodotto dalle colonne attive.

        Ritorna (prod_vals, barcode, cost, image_url, name_value, ref_value). Solo
        i valori NON vuoti finiscono nei vals (un giro non azzera campi con celle
        vuote). cost=None se la colonna costo non è tra le attive. ref_value è il
        riferimento interno (default_code) letto dal file, "" se non valorizzato.
        """
        prod_vals = {}
        barcode = False
        cost = None
        image_url = ""
        name_value = ""
        ref_value = ""
        dim_fields = self._dimension_fields()

        for spec in specs:
            value = row.get(spec["header"])
            if spec["kind"] == "field":
                field_name = spec.get("field_name")
                text = cp.to_text(value)
                if field_name and text:
                    prod_vals[field_name] = text
                continue
            target = spec.get("special")
            if target == "supplier_code":
                continue  # chiave: gestita dal chiamante
            elif target == "internal_reference":
                ref_value = cp.to_text(value)
                if ref_value:
                    prod_vals["default_code"] = ref_value
            elif target == "cost_netto":
                cost = cp.to_float(value)
            elif target == "barcode":
                barcode = cp.to_text(value) or False
                if barcode:
                    prod_vals["barcode"] = barcode
            elif target == "name":
                name_value = cp.to_text(value)
                if name_value:
                    prod_vals["name"] = name_value
            elif target == "weight_grams":
                grams = cp.to_float(value)
                if grams:
                    prod_vals["weight"] = grams / 1000.0
            elif target == "volume_cm3":
                vol = cp.to_float(value)
                if vol:
                    prod_vals["volume"] = vol / 1000000.0
            elif target in _DIMENSION_SPECIAL:
                field_name = dim_fields.get(_DIMENSION_SPECIAL[target])
                num = cp.to_float(value)
                if field_name and num:
                    prod_vals[field_name] = num
            elif target in _CATEGORY_SPECIAL:
                text = cp.to_text(value)
                if text:
                    prod_vals[_CATEGORY_SPECIAL[target]] = text
            elif target == "long_description":
                text = cp.clean_long_text(value)
                if text:
                    prod_vals[self._long_desc_field()] = text
            elif target == "image_url":
                image_url = cp.to_text(value) or ""
        return prod_vals, barcode, cost, image_url, name_value, ref_value

    def _create_product(self, code, prod_vals, name_value):
        """Crea un product.template dropship e ritorna la sua variante.

        `default_code` (riferimento interno) arriva dal file via prod_vals: il
        modulo NON lo genera più (spec §13, v2.2).
        """
        self.ensure_one()
        vals = dict(prod_vals)
        vals.update({
            "is_dropship": True,
            "dropship_supplier_id": self.dropship_supplier_id.id,
            "company_id": self.company_id.id,
        })
        # I prodotti dropship devono essere STOCCABILI: il Flusso B ne scrive la
        # giacenza come stock nativo (stock.quant), che Odoo RIFIUTA su
        # consumabili/servizi ("Quants cannot be created for consumables or
        # services"). Odoo 18 = Beni (consu) + is_storable=True; versioni
        # precedenti = type 'product'. Campo rilevato dinamicamente.
        tmpl_fields = self.env["product.template"]._fields
        if "is_storable" in tmpl_fields:
            vals["is_storable"] = True
        elif "detailed_type" in tmpl_fields:
            vals.setdefault("detailed_type", "product")
        else:
            vals.setdefault("type", "product")
        if not vals.get("name"):
            vals["name"] = name_value or code
        template = self.env["product.template"].create(vals)
        return template.product_variant_id

    def _download_images(self, image_jobs):
        """FASE 2 (per-CHUNK): scarica le immagini principali della slice.

        RESILIENTE: un URL fallito (incluso 403 di certe CDN) NON blocca — si
        logga e si prosegue, l'immagine resta vuota e viene ritentata a un import
        successivo. Usa header "da browser" + Referer del dominio (cfr. TASK_44).
        NON committa (lo fa il job chiamante). Ritorna (ok, failed).
        """
        if not image_jobs:
            return 0, 0
        import requests
        from urllib.parse import urlsplit

        Product = self.env["product.product"]
        ok = failed = 0
        for product_id, url in image_jobs:
            product = Product.browse(product_id)
            if not product.exists() or not url:
                continue
            try:
                headers = dict(_IMAGE_HTTP_HEADERS)
                parts = urlsplit(url)
                if parts.scheme and parts.netloc:
                    headers["Referer"] = "%s://%s/" % (parts.scheme, parts.netloc)
                response = requests.get(
                    url, timeout=_IMAGE_TIMEOUT, headers=headers)
                response.raise_for_status()
                product.image_1920 = base64.b64encode(response.content)
                ok += 1
            except Exception as exc:  # noqa: BLE001 — immagine non bloccante
                failed += 1
                self._log("sync_import_image", "error",
                          "Immagine non scaricata per %s (%s): %s" % (
                              product.default_code or product.id, url, exc))
        return ok, failed

    def _image_header(self, specs):
        """Intestazione della colonna 'URL immagine principale' tra le attive, o
        None se non mappata/attiva (→ niente FASE 2)."""
        return next(
            (s["header"] for s in specs
             if s["kind"] == "special" and s["special"] == "image_url"), None)

    def _download_images_for_rows(self, rows, specs):
        """FASE 2 (per-CHUNK) DERIVATA DAL FILE: per la slice di righe, scarica
        l'immagine dei prodotti già esistenti SOLO dove `image_1920` è vuoto.

        Indipendente dalla FASE 1: funziona anche su prodotti creati in un run
        precedente (ripresa/idempotenza, spec §9.2). Il match è per codice
        fornitore (invariato). Ritorna (ok, failed, skipped) — skipped = righe
        senza URL, prodotto non trovato o immagine già presente. NON committa.
        """
        self.ensure_one()
        image_header = self._image_header(specs)
        if not image_header:
            return 0, 0, len(rows)
        key_header = self._key_header(specs)
        jobs = []
        skipped = 0
        for row in rows:
            url = cp.to_text(row.get(image_header))
            if not url:
                skipped += 1
                continue
            code = cp.normalize_code(row.get(key_header))
            product = self._find_existing_product(code, False) if code else None
            if not product:
                skipped += 1
                continue
            if product.image_1920:
                skipped += 1  # già scaricata: idempotente, non riscarica
                continue
            jobs.append((product.id, url))
        ok, failed = self._download_images(jobs)
        return ok, failed, skipped

    # ------------------------------------------------------------------
    # FLUSSO B — aggiornamento giacenze (SFTP → stock Odoo del magazzino)
    #
    # La disponibilità del fornitore si scrive DIRETTAMENTE come stock Odoo
    # nativo nella giacenza-ubicazione del MAGAZZINO DEDICATO del fornitore
    # (spec §14): i feed marketplace la leggono per-magazzino senza ponti, e la
    # valorizzazione resta pulita (si valorizza solo il magazzino proprio).
    # Anti-fantasma (mai crea prodotti), prezzo IGNORATO, azzeramento dei codici
    # spariti. Esecuzione via cron esecutore + bottone (in background).
    # ------------------------------------------------------------------
    def action_run_stock_feed_now(self):
        """Bottone: accoda l'aggiornamento giacenze e innesca il cron esecutore.

        Non elabora nella request web (niente timeout sui file grandi, lezione
        TASK_84): imposta stock_feed_pending e triggera il cron, che lo elabora
        al primo tick. L'avanzamento si segue dal Log operazioni.
        """
        self.ensure_one()
        if not self.stock_warehouse_id:
            raise UserError(_(
                "Imposta il 'Magazzino giacenze' del fornitore prima di "
                "aggiornare le giacenze."))
        if not (self.sftp_host and self.path_giacenze):
            raise UserError(_(
                "Configura host SFTP e nome file giacenze prima di aggiornare."))
        if not (self.stock_code_header and self.stock_qty_header):
            raise UserError(_(
                "Configura 'Colonna codice' e 'Colonna disponibilità' del file "
                "giacenze prima di aggiornare."))
        self.write({"stock_feed_pending": True})
        cron = self.env.ref(
            "centrivo_sync_fornitori.cron_run_stock_feeds",
            raise_if_not_found=False)
        if cron:
            cron._trigger()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Aggiornamento giacenze avviato"),
                "message": _(
                    "Le giacenze di %s vengono aggiornate in background. "
                    "Segui l'esito dal Log operazioni e da 'Ultimo aggiornamento "
                    "giacenze'.") % self.name,
                "type": "success",
                "sticky": False,
            },
        }

    @api.model
    def cron_run_stock_feeds(self):
        """Cron ESECUTORE a CHUNK: un solo canale e un solo blocco per tick.

        Priorità: (1) riprende un feed già 'running'; altrimenti (2) avvia il
        canale con lancio manuale in coda (stock_feed_pending); altrimenti (3) un
        canale ricorrente 'attivo' non sincronizzato nell'ultimo intervallo. Si
        ri-arma finché resta un feed in corso (blocchi back-to-back, senza
        attendere l'intervallo periodico). Così nessun tick sfora il limite di
        tempo del worker anche su file grandi. Errori isolati per canale.
        """
        channel = self.search(
            [("stock_feed_state", "=", "running")], order="write_date", limit=1)
        starting = False
        if not channel:
            channel = self.search([
                ("stock_feed_pending", "=", True),
                ("stock_feed_state", "=", "idle")], order="write_date", limit=1)
            if not channel:
                threshold = fields.Datetime.now() - timedelta(
                    hours=_STOCK_RECUR_HOURS)
                channel = self.search([
                    ("stock_feed_active", "=", True),
                    ("stock_feed_state", "=", "idle"),
                    "|", ("last_stock_sync", "=", False),
                    ("last_stock_sync", "<", threshold)],
                    order="last_stock_sync", limit=1)
            starting = bool(channel)
        if not channel:
            return
        try:
            if starting:
                channel._start_stock_feed()
            channel._run_stock_feed_chunk()
        except Exception as exc:  # noqa: BLE001 — un canale non blocca il cron
            self.env.cr.rollback()
            channel.write({
                "stock_feed_state": "idle", "stock_feed_pending": False,
                "stock_feed_blob": False})
            channel._log("sync_stock", "error",
                         "Aggiornamento giacenze fallito: %s" % exc)
            _logger.exception("Stock feed canale %s fallito", channel.id)
            self.env.cr.commit()
        # Ri-arma finché resta un feed in corso (blocchi/canali back-to-back).
        if self.search_count([("stock_feed_state", "=", "running")]):
            cron = self.env.ref(
                "centrivo_sync_fornitori.cron_run_stock_feeds",
                raise_if_not_found=False)
            if cron:
                cron._trigger()

    def _stock_location(self):
        """Giacenza-ubicazione del magazzino del canale (dove scrivere lo stock)."""
        self.ensure_one()
        location = self.stock_warehouse_id.lot_stock_id
        if not location:
            raise UserError(_(
                "Il magazzino '%s' non ha una giacenza-ubicazione valida."
                % (self.stock_warehouse_id.display_name or "")))
        return location

    def _set_inventory_qty(self, product, location, qty):
        """Prepara la rettifica di giacenza (imposta inventory_quantity) e RITORNA
        il quant, SENZA applicare: l'apply avviene in BULK per blocco (una sola
        action_apply_inventory), molto più veloce che riga per riga.
        """
        Quant = self.env["stock.quant"].sudo().with_context(inventory_mode=True)
        quant = Quant.search([
            ("product_id", "=", product.id),
            ("location_id", "=", location.id),
        ], limit=1)
        if not quant:
            quant = Quant.create({
                "product_id": product.id,
                "location_id": location.id,
                "inventory_quantity": qty,
            })
        else:
            quant.inventory_quantity = qty
        return quant

    def _start_stock_feed(self):
        """Avvia un run: scarica lo SNAPSHOT del file, valida le colonne, azzera i
        contatori e passa in stato 'running'. Il download è UNA volta per run
        (coerenza tra i blocchi anche se il file cambia durante l'elaborazione).
        """
        self.ensure_one()
        from odoo.addons.integrations_core.connectors.transport import (
            TransportError)
        if not self.stock_warehouse_id:
            raise UserError(_("Magazzino giacenze non impostato."))
        if not (self.stock_code_header and self.stock_qty_header):
            raise UserError(_("Mappatura giacenze (codice/disponibilità) non impostata."))
        transport = self._build_transport()
        try:
            content = transport.download(self.path_giacenze)
        except TransportError as exc:
            raise UserError(_("Download giacenze fallito: %s") % exc)
        headers, rows = cp.read_table(content, self.path_giacenze)
        if self.stock_code_header not in headers:
            raise UserError(_(
                "Colonna codice '%s' assente nel file giacenze (intestazioni: %s)."
                % (self.stock_code_header, ", ".join(h for h in headers if h))))
        if self.stock_qty_header not in headers:
            raise UserError(_(
                "Colonna disponibilità '%s' assente nel file giacenze."
                % self.stock_qty_header))
        self.write({
            "stock_feed_state": "running",
            "stock_feed_pending": False,
            "stock_feed_blob": base64.b64encode(content),
            "stock_feed_cursor": 0,
            "stock_feed_total": len(rows),
            "stock_feed_matched_json": "[]",
            "stock_feed_updated_count": 0,
            "stock_feed_notfound_count": 0,
        })
        self.env.cr.commit()
        self._log("sync_stock", "success",
                  "Avvio aggiornamento giacenze: %s righe da elaborare a blocchi."
                  % len(rows))

    def _run_stock_feed_chunk(self):
        """Elabora UN blocco di righe del file-snapshot dal cursore corrente.

        Match per codice fornitore; rettifica giacenza in BULK (una apply per
        blocco); anti-fantasma (conta, NON logga riga per riga); traccia supply.
        A fine file: azzeramento dei codici spariti (con guardia) e riepilogo.
        NON committa dentro il loop: committa il cursore a fine blocco.
        """
        self.ensure_one()
        if self.stock_feed_state != "running":
            return
        content = base64.b64decode(self.stock_feed_blob or b"")
        if not content:
            raise ValueError(_("Snapshot file giacenze mancante."))
        _headers, rows = cp.read_table(content, self.path_giacenze)
        location = self._stock_location()
        start = self.stock_feed_cursor
        end = min(start + _STOCK_ROW_CHUNK, len(rows))
        matched_ids = set(json.loads(self.stock_feed_matched_json or "[]"))
        updated = notfound = 0
        quants = self.env["stock.quant"].sudo().with_context(
            inventory_mode=True).browse()
        supply_touch = []  # [(product, code, qty)] — applicato dopo l'apply bulk
        for row in rows[start:end]:
            code = cp.normalize_code(row.get(self.stock_code_header))
            if not code:
                continue
            qty = cp.to_float(row.get(self.stock_qty_header))
            product = self._find_existing_product(code, False)
            if not product:
                notfound += 1  # anti-fantasma: conteggiato, non loggato
                continue
            quants |= self._set_inventory_qty(product, location, qty)
            supply_touch.append((product, code, qty))
            matched_ids.add(product.id)
            updated += 1
        if quants:
            quants.action_apply_inventory()
        for product, code, qty in supply_touch:
            self._touch_supply(product, code, qty)
        self.write({
            "stock_feed_cursor": end,
            "stock_feed_updated_count": self.stock_feed_updated_count + updated,
            "stock_feed_notfound_count": self.stock_feed_notfound_count + notfound,
            "stock_feed_matched_json": json.dumps(sorted(matched_ids)),
        })
        self.env.cr.commit()
        if end < len(rows):
            self._log("sync_stock", "success",
                      "Progresso giacenze: righe %s/%s, aggiornate %s finora."
                      % (end, len(rows), self.stock_feed_updated_count))
        else:
            self._finish_stock_feed(location, matched_ids)

    def _finish_stock_feed(self, location, matched_ids):
        """Chiude il run: azzera i codici spariti (con guardia anti-wipe), timbra
        l'ultimo sync, logga il riepilogo e torna 'idle' liberando lo snapshot.

        GUARDIA: se NESSUN codice del file ha trovato corrispondenza (probabile
        errore di mappatura/fornitore), NON azzera — eviterebbe di cancellare
        tutte le giacenze per una misconfigurazione.
        """
        self.ensure_one()
        zeroed = 0
        if matched_ids:
            stale = self.env["stock.quant"].sudo().with_context(
                inventory_mode=True).search([
                    ("location_id", "=", location.id),
                    ("quantity", ">", 0),
                    ("product_id", "not in", list(matched_ids)),
                ])
            for quant in stale:
                quant.inventory_quantity = 0.0
            if stale:
                stale.action_apply_inventory()
                zeroed = len(stale)
        elif self.stock_feed_total:
            self._log("sync_stock", "error",
                      "Nessun codice del file giacenze trovato in Odoo: "
                      "azzeramento SALTATO (verifica mappatura colonne/fornitore).")
        self.write({
            "last_stock_sync": fields.Datetime.now(),
            "stock_feed_state": "idle",
            "stock_feed_blob": False,
            "stock_feed_pending": False,
        })
        self.env.cr.commit()
        self._log("sync_stock", "success",
                  "Giacenze aggiornate: %s prodotti, %s azzerati, %s codici non "
                  "trovati (su %s righe)." % (
                      self.stock_feed_updated_count, zeroed,
                      self.stock_feed_notfound_count, self.stock_feed_total))

    def _touch_supply(self, product, code, qty):
        """Aggiorna la TRACCIA centrivo.sync.supply (NON è più la giacenza, spec
        §14.1): mirror informativo + last_sync per audit per-fornitore."""
        self.ensure_one()
        Supply = self.env["centrivo.sync.supply"]
        existing = Supply.search([
            ("product_id", "=", product.id),
            ("dropship_supplier_id", "=", self.dropship_supplier_id.id),
            ("company_id", "=", self.company_id.id),
        ], limit=1)
        vals = {"qty_available": qty, "last_sync": fields.Datetime.now(),
                "channel_id": self.id, "supplier_sku": code}
        if existing:
            existing.write(vals)
        else:
            vals.update({
                "product_id": product.id,
                "dropship_supplier_id": self.dropship_supplier_id.id,
                "company_id": self.company_id.id,
            })
            Supply.create(vals)
