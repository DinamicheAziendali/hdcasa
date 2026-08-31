# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Le righe Kaufland si raggiungono DALL'ORDINE, non da un menu suo.

⚠️ Scelta di Angelo il 2026-08-31, guardando la barra: «Righe d'ordine» come
voce di menu era una voce in piu' per un'informazione che ha senso solo dentro
un ordine. Il pulsante la porta dove serve e toglie una riga dalla barra — che
e' il problema che stavamo risolvendo.
"""
from odoo import _, api, fields, models


class CentrivoOrderMap(models.Model):
    _inherit = "centrivo.order.map"

    # ⚠️ Un One2many non aggiunge nessuna colonna a `centrivo.order.map`, che in
    # produzione porta gli ordini di ManoMano e BricoBravo: lo schema del
    # tronco resta quello che e'.
    kaufland_unit_ids = fields.One2many(
        "centrivo.kaufland.order.unit", "order_map_id",
        string="Righe Kaufland")
    kaufland_unit_count = fields.Integer(
        string="Righe Kaufland", compute="_compute_kaufland_unit_count")

    @api.depends("kaufland_unit_ids")
    def _compute_kaufland_unit_count(self):
        for mappa in self:
            mappa.kaufland_unit_count = len(mappa.kaufland_unit_ids)

    def action_kaufland_vedi_righe(self):
        """Apre le righe Kaufland di QUESTO ordine."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Righe Kaufland di %s") % self.external_id,
            "res_model": "centrivo.kaufland.order.unit",
            "view_mode": "list,form",
            "domain": [("order_map_id", "=", self.id)],
            "context": {"default_order_map_id": self.id},
        }

    def action_push_shipment(self):
        """Il pulsante e' il gesto A MANO: passa oltre il cancello.

        ⚠️ Il connettore Kaufland si rifiuta di comunicare la spedizione
        finche' nessuno ha verificato sul portale che il primo invio funzioni
        (vedi `kaufland_spedizione_provata`). Quel rifiuto vale per il CRON,
        non per la persona che sta premendo: il primo invio e' proprio questo
        gesto, e senza di lui il cancello non si aprirebbe mai.

        Gli altri marketplace non vengono toccati: si devia solo Kaufland.
        """
        kaufland = self.filtered(
            lambda m: m.channel_id.connector_code == "kaufland")
        if kaufland:
            super(CentrivoOrderMap, kaufland.with_context(
                kaufland_spedizione_a_mano=True)).action_push_shipment()
        altri = self - kaufland
        if altri:
            return super(CentrivoOrderMap, altri).action_push_shipment()
        return True
