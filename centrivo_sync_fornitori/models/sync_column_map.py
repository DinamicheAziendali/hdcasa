# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sync.column.map — riga di mappatura colonna SALVATA per fornitore.

Cuore della mappatura DINAMICA (spec v2.x): una riga per colonna mappata di quel
fornitore (canale), salvata e ri-proposta ai caricamenti successivi dal wizard.
Nessun formato è cablato nel codice: qualunque struttura di file si mappa qui.
"""
from odoo import api, fields, models

# Destinazioni SPECIALI (semantiche, con trattamento automatico). Condivise tra
# column.map, wizard line ed engine d'import (sync_channel). Vedi spec §7.a.
SPECIAL_TARGETS = [
    ("supplier_code", "Codice fornitore (CHIAVE)"),
    ("internal_reference", "Riferimento interno"),
    ("cost_netto", "Costo d'acquisto (Netto)"),
    ("barcode", "Barcode / EAN"),
    ("name", "Nome prodotto"),
    ("weight_grams", "Peso (grammi → kg)"),
    ("volume_cm3", "Volume (cm³ → m³)"),
    ("dimension_l", "Lunghezza (cm)"),
    ("dimension_w", "Larghezza (cm)"),
    ("dimension_h", "Altezza (cm)"),
    ("image_url", "URL immagine principale"),
    ("supplier_cat_1", "Categoria fornitore — livello 1"),
    ("supplier_cat_2", "Categoria fornitore — livello 2"),
    ("supplier_cat_3", "Categoria fornitore — livello 3"),
    ("long_description", "Descrizione lunga / Nota"),
]

# Destinazioni speciali che scrivono su un campo di product.template (per la
# costruzione dei vals nell'engine). Le altre (supplier_code/cost_netto/image_url)
# hanno trattamento dedicato fuori dai vals prodotto.
DESTINATION_KIND = [
    ("special", "Destinazione speciale"),
    ("field", "Campo prodotto"),
    ("ignore", "Ignora"),
]

# Dominio per il campo prodotto generico (copia as-is): solo campi testuali
# scrivibili di product.template (evita errori di tipo).
PRODUCT_FIELD_DOMAIN = (
    "[('model', '=', 'product.template'), "
    "('ttype', 'in', ['char', 'text', 'html']), ('store', '=', True)]")


class CentrivoSyncColumnMap(models.Model):
    _name = "centrivo.sync.column.map"
    _description = "Mappatura colonna file → destinazione (per fornitore)"
    _order = "sequence, id"
    _rec_name = "source_header"

    channel_id = fields.Many2one(
        "centrivo.sync.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="channel_id.company_id", string="Azienda", store=True)
    sequence = fields.Integer(string="Sequenza", default=10)

    source_header = fields.Char(
        string="Colonna file", required=True,
        help="Intestazione REALE della colonna nel file del fornitore.")
    destination_kind = fields.Selection(
        selection=DESTINATION_KIND, string="Tipo destinazione",
        required=True, default="ignore")
    special_target = fields.Selection(
        selection=SPECIAL_TARGETS, string="Destinazione speciale",
        help="Valorizzato se Tipo = Destinazione speciale.")
    dest_field_id = fields.Many2one(
        "ir.model.fields", string="Campo prodotto",
        domain=PRODUCT_FIELD_DOMAIN, ondelete="set null",
        help="Valorizzato se Tipo = Campo prodotto (valore copiato as-is).")
    active_in_run = fields.Boolean(
        string="Attiva", default=True,
        help="Default di attivazione della colonna. Nel wizard si può "
             "attivare/disattivare per il singolo giro.")

    _sql_constraints = [
        ("uniq_channel_header", "unique(channel_id, source_header)",
         "Colonna già mappata per questo fornitore."),
    ]

    @api.onchange("destination_kind")
    def _onchange_destination_kind(self):
        """Pulisce i campi non pertinenti al tipo scelto (coerenza UI)."""
        if self.destination_kind != "special":
            self.special_target = False
        if self.destination_kind != "field":
            self.dest_field_id = False
