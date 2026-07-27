# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Campi del feed prodotto ManoMano, scaricati dalla Taxonomy API.

Catalogo GLOBALE (nessun company_id): è un vocabolario di piattaforma, non un
dato aziendale — stesso criterio degli stati di centrivo_shipment_tracking.
"""
from odoo import api, fields, models

from ..connectors.taxonomy_parser import parse_field, pair_values


class ManoManoFeedField(models.Model):
    _name = "centrivo.manomano.feed.field"
    _description = "Campo feed prodotto ManoMano"
    _order = "sequence, name"

    mm_id = fields.Char(
        string="ID ManoMano", index=True, copy=False,
        help="Identificativo del campo presso ManoMano: chiave di "
             "sincronizzazione. Vuoto sui campi creati prima del primo scarico.")
    name = fields.Char(
        string="Colonna", required=True, index=True,
        help="Nome tecnico del campo = intestazione della colonna nel CSV.")
    label = fields.Char(string="Etichetta")
    description = fields.Text(string="Spiegazione ManoMano")
    mandatory = fields.Boolean(string="Obbligatorio")
    datatype = fields.Char(string="Tipo di dato")
    contract_type = fields.Char(
        string="Vale per",
        help="all = tutti; non_mf = venditore che spedisce da sé; "
             "mf = logistica ManoMano (non ci riguarda).")
    is_variant = fields.Boolean(string="Di variante")
    units_list_id = fields.Char(string="Lista unità di misura")
    value_ids = fields.One2many(
        "centrivo.manomano.feed.field.value", "field_id",
        string="Valori ammessi")
    has_values = fields.Boolean(
        string="Lista chiusa", compute="_compute_has_values", store=True)
    sequence = fields.Integer(string="Sequenza", default=10)
    active = fields.Boolean(string="Attivo", default=True)

    _sql_constraints = [
        ("uniq_name", "unique(name)",
         "Esiste già un campo ManoMano con questo nome."),
    ]

    @api.depends("value_ids")
    def _compute_has_values(self):
        for record in self:
            record.has_values = bool(record.value_ids)

    @api.model
    def upsert_from_api(self, entries):
        """Allinea i campi ai dati API. Non cancella MAI: disattiva.

        Chiave primaria = mm_id. Ripiego sul nome per adottare i record creati
        dalla migrazione (che non hanno mm_id): così la configurazione fatta
        prima del primo scarico non si perde.

        La ricerca dei record esistenti vede anche quelli disattivati da una
        sincronia precedente: se ManoMano torna a esporre un campo che avevamo
        disattivato, lo si ritrova e lo si riattiva, invece di sbattere contro
        il vincolo di unicità sul nome tentando di crearne uno nuovo.
        """
        created = updated = 0
        anomalies = []
        seen_ids = []
        for position, entry in enumerate(entries):
            vals = parse_field(entry)
            if not vals["name"]:
                continue
            vals["sequence"] = (position + 1) * 10
            if vals["mm_id"]:
                record = self.with_context(active_test=False).search(
                    [("mm_id", "=", vals["mm_id"])], limit=1)
            else:
                record = self.browse()
            if not record:
                record = self.with_context(active_test=False).search(
                    [("name", "=", vals["name"]), ("mm_id", "in", (False, ""))],
                    limit=1)
            if record:
                vals["active"] = True
                record.write(vals)
                updated += 1
            else:
                record = self.create(vals)
                created += 1
            seen_ids.append(record.id)
            anomaly = record._sync_values(entry)
            if anomaly:
                anomalies.append(anomaly)

        deactivated = 0
        if seen_ids:
            obsolete = self.search([("id", "not in", seen_ids),
                                    ("mm_id", "not in", (False, ""))])
            if obsolete:
                obsolete.write({"active": False})
                deactivated = len(obsolete)

        # Segnaposto MAI adottati: restano attivi apposta (per non buttare la
        # configurazione fatta a mano), ma se non corrispondono a nessun nome
        # della taxonomy scaricata (colonna rinominata da ManoMano rispetto al
        # vecchio file Excel) restano orfani per sempre e nessuno se ne
        # accorgerebbe senza segnalarli qui.
        orfani = self.search([("mm_id", "in", (False, ""))]).mapped("name")

        return {"created": created, "updated": updated,
                "deactivated": deactivated, "anomalies": anomalies,
                "orfani": orfani}

    def _sync_values(self, entry):
        """Riallinea i valori ammessi. Chiave stabile = name_source (inglese)."""
        self.ensure_one()
        pairs, anomaly = pair_values(entry)
        existing = {v.name_source: v for v in self.value_ids}
        keep = []
        for position, (localized, source) in enumerate(pairs):
            value = existing.get(source)
            if value:
                value.write({"name": localized, "sequence": (position + 1) * 10})
            else:
                value = self.env["centrivo.manomano.feed.field.value"].create({
                    "field_id": self.id, "name": localized,
                    "name_source": source, "sequence": (position + 1) * 10})
            keep.append(value.id)
        obsolete = self.value_ids.filtered(lambda v: v.id not in keep)
        if obsolete:
            obsolete.unlink()
        return anomaly


class ManoManoFeedFieldValue(models.Model):
    _name = "centrivo.manomano.feed.field.value"
    _description = "Valore ammesso di un campo feed ManoMano"
    _order = "sequence, name"

    field_id = fields.Many2one(
        "centrivo.manomano.feed.field", string="Campo", required=True,
        ondelete="cascade", index=True)
    name = fields.Char(
        string="Valore", required=True,
        help="Valore nella lingua del feed: è ciò che finisce nel CSV.")
    name_source = fields.Char(
        string="Valore originale", required=True,
        help="Valore come lo restituisce ManoMano (inglese): chiave stabile.")
    sequence = fields.Integer(string="Sequenza", default=10)
