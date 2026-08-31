# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.override — eccezioni al codice corriere di un marketplace.

Normalmente questa tabella è VUOTA: la traduzione corriere → codice la dichiara
il connettore. Serve il giorno in cui un marketplace cambia un codice e non si
può attendere un rilascio del modulo: la riga qui vince su tutto.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..connectors.base import MarketplaceConnector


class CentrivoCarrierOverride(models.Model):
    _name = "centrivo.carrier.override"
    _description = "Eccezione codice corriere per marketplace"
    _order = "channel_id, brand_id"

    # ondelete="cascade" su ENTRAMBI, ed è voluto che diverga dal "restrict"
    # usato in centrivo.carrier.source: un'eccezione è solo una deroga a una
    # traduzione, quindi senza il suo canale o senza il suo corriere non
    # significa più niente e va via con loro. Il collegamento vettore →
    # corriere, invece, è la configurazione che l'utente ha costruito: lì il
    # "restrict" impedisce di cancellare un corriere ancora in uso.
    channel_id = fields.Many2one(
        "centrivo.channel", string="Canale", required=True,
        ondelete="cascade", index=True)
    brand_id = fields.Many2one(
        "centrivo.carrier.brand", string="Corriere", required=True,
        ondelete="cascade", index=True)
    external_code = fields.Selection(
        selection="_selection_external_code",
        string="Codice corriere marketplace", required=True,
        help="Codice da usare al posto di quello previsto dal connettore. Le "
             "opzioni sono la lista chiusa dichiarata dal connettore del "
             "canale.")
    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company, index=True)

    _sql_constraints = [
        ("carrier_override_uniq",
         "unique(channel_id, brand_id, company_id)",
         "Esiste già un'eccezione per questo corriere su questo canale."),
    ]

    @api.model
    def _selection_external_code(self):
        """Unione dei codici di TUTTI i connettori.

        Un campo Selection deve dichiarare ogni valore memorizzabile, su
        qualunque canale; la restrizione al singolo connettore è applicata dal
        vincolo qui sotto.
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
        un altro connettore (es. scelto BricoBravo e poi ripensato in
        ManoMano): l'errore verrebbe comunque intercettato dal vincolo al
        salvataggio, ma qui lo si previene mentre l'utente sta ancora
        compilando.
        """
        if self.external_code and self.channel_id:
            if self.external_code not in self._codes_for_channel(self.channel_id):
                self.external_code = False

    @api.constrains("external_code", "channel_id")
    def _check_external_code(self):
        """Il codice deve appartenere al connettore del canale."""
        for record in self:
            validi = record._codes_for_channel(record.channel_id)
            if record.external_code and record.external_code not in validi:
                raise ValidationError(
                    "Il codice corriere '%s' non è valido per il connettore "
                    "del canale '%s'. Codici ammessi: %s."
                    % (record.external_code, record.channel_id.name,
                       ", ".join(sorted(validi)) or "nessuno"))

    @api.model
    def resolve_code(self, channel, brand, company):
        """Codice dell'eccezione per quel canale e corriere ('' se assente)."""
        if not channel or not brand:
            return ""
        record = self.search([
            ("channel_id", "=", channel.id),
            ("brand_id", "=", brand.id),
            ("company_id", "=", company.id),
        ], limit=1)
        return record.external_code or ""
