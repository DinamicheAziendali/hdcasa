# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.map — mapping corriere Odoo <-> codice corriere del marketplace.

Generico e per-canale: lo stesso delivery.carrier Odoo può corrispondere a codici
diversi su marketplace diversi, quindi il mapping è specifico del canale.

Il codice esterno (`external_code`) NON è testo libero: è un Selection le cui
opzioni sono dichiarate DAL CONNETTORE del canale (vedi
MarketplaceConnector.carrier_codes). Selezionando un canale BricoBravo si scelgono
solo i codici BricoBravo; quando arriveranno altri connettori (ManoMano, ecc.)
ciascuno dichiarerà la propria lista senza toccare questo modello.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..connectors.base import MarketplaceConnector


class IntegrationCarrierMap(models.Model):
    _name = "centrivo.carrier.map"
    _description = "Mapping corriere verso codice corriere del marketplace"

    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True,
        help="Il mapping è specifico del canale: lo stesso corriere Odoo può "
             "mappare a codici diversi su marketplace diversi.")

    carrier_id = fields.Many2one(
        "delivery.carrier", string="Corriere Odoo", required=True,
        ondelete="cascade")

    # Selection DINAMICO: le opzioni sono i codici dichiarati dai connettori.
    # Vedi nota in _selection_external_code sul perché si ritorna l'UNIONE.
    external_code = fields.Selection(
        selection="_selection_external_code",
        string="Codice corriere marketplace", required=True,
        help="Codice del corriere come atteso dal marketplace. Le opzioni "
             "dipendono dal connettore del canale selezionato (lista chiusa).")

    tracking_url_template = fields.Char(
        string="Template URL tracking", required=True,
        help="URL di tracciamento con il segnaposto {tracking}, che verrà "
             "sostituito dal numero di tracking. "
             "Esempio: https://vivi.brt.it/?tracking={tracking}")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_channel_carrier_company",
         "unique(channel_id, carrier_id, company_id)",
         "Esiste già un mapping per questo corriere su questo canale e azienda."),
    ]

    @api.model
    def _selection_external_code(self):
        """Opzioni del Selection `external_code`: UNIONE dei codici di tutti i connettori.

        Un campo Selection deve dichiarare TUTTI i valori memorizzabili (su
        qualunque canale), altrimenti Odoo marca invalidi i valori non in lista.
        La restrizione "su un canale BricoBravo si scelgono SOLO i codici
        BricoBravo" è applicata dall'onchange (_onchange_channel_id) e dal vincolo
        (_check_external_code). Oggi il solo connettore registrato è BricoBravo,
        quindi qui compaiono esattamente i suoi 16 codici.
        """
        codes = MarketplaceConnector.get_all_carrier_codes()
        return codes or [("other", "Altro")]

    def _codes_for_channel(self, channel):
        """Dict {codice: etichetta} dei codici validi per il connettore del canale."""
        if not channel:
            return {}
        return dict(MarketplaceConnector.get_carrier_codes_for(channel.connector_code))

    @api.onchange("channel_id")
    def _onchange_channel_id(self):
        """Quando cambia il canale, azzera un external_code non più valido.

        Così, cambiando canale, non resta selezionato un codice appartenente a
        un altro connettore. (Il web client mostra l'unione dei codici; il
        vincolo garantisce comunque la coerenza al salvataggio.)
        """
        if self.external_code and self.channel_id:
            if self.external_code not in self._codes_for_channel(self.channel_id):
                self.external_code = False

    @api.constrains("external_code", "channel_id")
    def _check_external_code(self):
        """Garantisce che external_code appartenga al connettore del canale."""
        for rec in self:
            valid = rec._codes_for_channel(rec.channel_id)
            if rec.external_code and rec.external_code not in valid:
                raise ValidationError(
                    "Il codice corriere '%s' non è valido per il connettore "
                    "del canale '%s'. Codici ammessi: %s."
                    % (rec.external_code, rec.channel_id.name,
                       ", ".join(sorted(valid)) or "nessuno"))
