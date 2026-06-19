# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sla.zone — zona di consegna configurabile per gli SLA (spec §3.1).

Una zona è definita da liste di NAZIONI, PROVINCE/STATI (res.country.state) e CAP
(liste/range). In Odoo la "regione" italiana non è nativa: res.country.state copre
province e stati esteri, e per Isole/località remote bastano province + CAP (nodo
§11.6 della specifica). La zona di una spedizione si risolve in modo deterministico
con priorità dal più specifico al più generico: CAP > provincia/stato > nazione
(il match più specifico vince). Nessun match → spedizione "non zonizzata".

Modello transazionale di configurazione con company_id + record rule.
"""
from odoo import api, fields, models


class CentrivoSlaZone(models.Model):
    _name = "centrivo.sla.zone"
    _description = "Zona di consegna SLA"
    _order = "sequence, name, id"

    name = fields.Char(string="Nome zona", required=True, translate=True)
    sequence = fields.Integer(
        string="Sequenza", default=10,
        help="A parità di specificità del match, vince la zona con sequenza più "
             "bassa (poi id più basso).")
    active = fields.Boolean(string="Attivo", default=True)

    # --- Criteri geografici (livelli dal più generico al più specifico) ----
    country_ids = fields.Many2many(
        "res.country", "centrivo_sla_zone_country_rel", "zone_id", "country_id",
        string="Nazioni",
        help="Nazioni di destinazione coperte dalla zona. Vuoto = qualunque nazione.")
    state_ids = fields.Many2many(
        "res.country.state", "centrivo_sla_zone_state_rel", "zone_id", "state_id",
        string="Province / Stati",
        help="Province (res.country.state) o stati esteri coperti. In Italia la "
             "provincia (es. PA, CT) è lo state_id. Vuoto = qualunque provincia.")
    zip_codes = fields.Char(
        string="CAP (liste/range)",
        help="Liste e/o range di CAP separati da virgola o a capo. Esempi:\n"
             "  90100-92100  (range numerico)\n"
             "  09100, 07026  (CAP singoli)\n"
             "Vuoto = qualunque CAP. Per CAP non numerici (estero) il match è "
             "esatto; i range valgono solo per CAP numerici.")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    # ==================================================================
    # Matching e risoluzione zona
    # ==================================================================
    def _parse_zip_tokens(self):
        """Ritorna i token CAP normalizzati (lista di stringhe non vuote)."""
        self.ensure_one()
        raw = (self.zip_codes or "").replace("\n", ",").replace(";", ",")
        return [t.strip() for t in raw.split(",") if t.strip()]

    def _zip_match(self, zipcode):
        """True se il CAP della spedizione rientra nei token della zona."""
        self.ensure_one()
        tokens = self._parse_zip_tokens()
        if not tokens:
            return True  # criterio non vincolante
        if not zipcode:
            return False
        target = zipcode.strip().upper()
        for tok in tokens:
            tok = tok.upper()
            if "-" in tok:
                lo, _, hi = tok.partition("-")
                lo, hi = lo.strip(), hi.strip()
                if lo.isdigit() and hi.isdigit() and target.isdigit():
                    if int(lo) <= int(target) <= int(hi):
                        return True
                else:
                    # Range non numerico: confronto lessicografico difensivo.
                    if lo <= target <= hi:
                        return True
            elif tok == target:
                return True
        return False

    def _specificity(self):
        """Livello di specificità della zona (CAP=3 > provincia=2 > nazione=1)."""
        self.ensure_one()
        if self.zip_codes and self._parse_zip_tokens():
            return 3
        if self.state_ids:
            return 2
        if self.country_ids:
            return 1
        return 0

    def _match_shipment(self, shipment):
        """True se TUTTI i criteri NON vuoti della zona combaciano con la spedizione."""
        self.ensure_one()
        if self.country_ids and shipment.dest_country_id not in self.country_ids:
            return False
        if self.state_ids and shipment.dest_state_id not in self.state_ids:
            return False
        if self.zip_codes and self._parse_zip_tokens():
            if not self._zip_match(shipment.dest_zip):
                return False
        # Una zona senza alcun criterio non matcha (non è una catch-all).
        return self._specificity() > 0

    @api.model
    def _resolve_zone(self, shipment):
        """Zona della spedizione: match più specifico vince (spec §3.1).

        PRIORITÀ: CAP > provincia/stato > nazione. A parità di specificità vince la
        sequenza più bassa (le zone sono lette ordinate per sequence, id). Nessun
        match → recordset vuoto (spedizione "non zonizzata": le regole con zona
        vuota restano comunque applicabili).
        """
        zones = self.search([
            ("active", "=", True),
            "|", ("company_id", "=", False),
            ("company_id", "=", shipment.company_id.id),
        ])
        best = self.browse()
        best_spec = -1
        for zone in zones:
            if not zone._match_shipment(shipment):
                continue
            spec = zone._specificity()
            if spec > best_spec:
                best = zone
                best_spec = spec
        return best
