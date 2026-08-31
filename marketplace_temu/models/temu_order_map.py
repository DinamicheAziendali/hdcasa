# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il bottone che rilegge da Temu lo stato di una spedizione gia' comunicata.

⚠️ Sta qui e non sul canale perche' `check_shipment` riguarda UN ordine: e'
la domanda «questa spedizione, Temu l'ha presa?», che si fa davanti a un
ordine fermo, non davanti al canale.

⚠️ `_inherit` estende il modello di integrations_core senza modificarlo: quel
modulo e' in produzione sotto ManoMano e BricoBravo, e non si tocca.
"""
from odoo import _, fields, models
from odoo.exceptions import UserError

from odoo.addons.integrations_core.connectors.base import MarketplaceConnector


class CentrivoOrderMap(models.Model):
    _inherit = "centrivo.order.map"

    # ⚠️ Serve SOLO a far nascondere il bottone qui sotto sugli ordini che non
    # sono Temu: un `invisible` di vista non attraversa la relazione, quindi
    # `channel_id.connector_code` da li' non si legge.
    #
    # ⚠️ SENZA `store=True`, ed e' la ragione per cui questa riga si puo'
    # scrivere: un `related` non memorizzato e' calcolato al volo e NON
    # aggiunge nessuna colonna a `centrivo.order.map`, che in produzione porta
    # gli ordini di ManoMano e BricoBravo. Lo schema del nucleo resta quello
    # che e'. E' lo stesso modo in cui questo modulo estende gia' il canale.
    #
    # ⚠️ E' una `Selection`, non una `Char`, e non e' un dettaglio di stile:
    # `centrivo.channel.connector_code` e' una Selection, e Odoo pretende che
    # un `related` abbia lo STESSO tipo del campo che segue. Scritta Char,
    # questa riga faceva morire l'intero avvio con
    # «Type of related field centrivo.order.map.connector_code is inconsistent
    # with centrivo.channel.connector_code» — cioe' nessun modulo si
    # aggiornava, non solo Temu. Misurato sullo stage il 2026-08-28: e' il
    # primo difetto che un Odoo vero ha trovato e che 35 banchi verdi non
    # potevano vedere, perche' nessuno di loro carica i modelli.
    # La lista dei valori non si ripete: un `related` la eredita dall'origine.
    connector_code = fields.Selection(
        related="channel_id.connector_code", readonly=True,
        string="Codice connettore")

    def action_temu_check_shipment(self):
        """Rilegge da Temu lo stato della spedizione di questo ordine."""
        self.ensure_one()
        if self.channel_id.connector_code != "temu":
            raise UserError(_(
                "Questo ordine non e' di un canale Temu: e' di «%s».")
                % (self.channel_id.name or ""))
        connector = MarketplaceConnector.for_channel(self.channel_id)
        letto = connector.check_shipment(self)
        return self.channel_id._temu_notification(
            "Stato spedizione riletto da Temu. Dettaglio nel Log operazioni."
            if letto else
            "Non si e' potuto rileggere lo stato: il motivo e' nel Log "
            "operazioni.",
            kind="success" if letto else "warning")
