# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.order.map — registro degli ordini importati (idempotenza).

Lega un identificativo ordine ESTERNO (external_id) + canale al sale.order Odoo
creato. Serve a due cose fondamentali:
  - NON duplicare: se un ordine è già stato importato, lo si salta;
  - NON perdere: se l'import va in errore, resta traccia (stato error + messaggio)
    e si può ritentare.
"""
from odoo import fields, models


class IntegrationOrderMap(models.Model):
    _name = "centrivo.order.map"
    _description = "Registro ordini importati dai marketplace"
    _order = "create_date desc"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)

    # Identificativo dell'ordine sul marketplace (per BricoBravo: id_order o
    # vtex_id_order). La coppia (channel_id, external_id) è univoca.
    external_id = fields.Char(string="ID ordine esterno", required=True, index=True)

    # Riferimento all'ordine Odoo creato (vuoto finché non creato / in errore).
    sale_order_id = fields.Many2one(
        "sale.order", string="Ordine Odoo", ondelete="set null")

    state = fields.Selection(
        selection=[
            ("pending", "In attesa"),
            ("imported", "Importato"),
            ("error", "Errore"),
        ],
        string="Stato", default="pending", required=True, index=True)

    error_message = fields.Text(string="Messaggio di errore")

    # Strato 3a (mark_acquired): True quando l'ordine importato è stato marcato
    # "acquisito" sul marketplace (BricoBravo: PATCH /orders/{id}/acquired).
    # Significato deciso da Angelo: "acquisito = importato correttamente in Odoo".
    # Se lo stato è "imported" ma acquired_done=False, l'acquired è ancora PENDENTE
    # (la chiamata non è andata a buon fine) e verrà ritentato al prossimo pull.
    acquired_done = fields.Boolean(
        string="Acquisito su marketplace", default=False, index=True,
        help="True quando l'ordine importato è stato marcato 'acquisito' sul "
             "marketplace. Se False con stato 'Importato', l'acquired è pendente "
             "e verrà ritentato.")

    # Strato 3b (push_shipment): True SOLO quando la spedizione (corriere +
    # tracking) è stata comunicata con successo al marketplace (BricoBravo:
    # PATCH /orders/{id}/shipped → data.updated == true). Stessa identica
    # semantica di acquired_done: se False, il push è ancora da fare/ritentare.
    shipment_pushed = fields.Boolean(
        string="Spedizione comunicata", default=False, index=True,
        help="True quando corriere e tracking sono stati comunicati con "
             "successo al marketplace. Se False, il push spedizione non è "
             "ancora andato a buon fine.")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_channel_external",
         "unique(channel_id, external_id)",
         "Questo ordine esterno è già registrato per il canale."),
    ]

    def action_push_shipment(self):
        """Trigger MANUALE: comunica la spedizione al marketplace per gli ordini.

        Richiamabile dal bottone sul form e dall'azione server sulla lista
        (selezione multipla). Per ogni order.map istanzia il connettore del suo
        canale e ne invoca push_shipment(order_map). Le precondizioni (stato,
        picking done con tracking, mapping corriere) e l'idempotenza sono gestite
        dal connettore: qui non si forza nulla. Nessun automatismo: solo manuale.
        """
        for order_map in self:
            connector = order_map.channel_id._get_connector()
            connector.push_shipment(order_map)
        return True
