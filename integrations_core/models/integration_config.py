# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Configurazione globale delle integrazioni marketplace.

Oggi contiene una sola impostazione: da quale campo del trasferimento leggere il
VETTORE per il push spedizioni. Serve perché in produzione il vettore non sta nel
`carrier_id` nativo ma in un campo di terzi (es. `transport_carrier_id` di
Dinamiche Aziendali).

L'impostazione è UNICA e non per canale: dove sta il vettore sul trasferimento
non dipende da quale marketplace ha generato l'ordine. Il mapping vettore →
codice marketplace resta invece per canale (centrivo.carrier.map).

Il campo si legge in modo DINAMICO e OPZIONALE: nessuna dipendenza hard da
moduli di terzi, e a impostazione vuota si usa il nativo — comportamento
identico a prima di questa configurazione.
"""
from odoo import api, fields, models

# Campo usato quando la configurazione è vuota: il corriere nativo di Odoo.
# Garantisce la non-regressione su tutte le installazioni esistenti.
DEFAULT_CARRIER_SOURCE_FIELD = "carrier_id"


class CentrivoIntegrationConfig(models.Model):
    _name = "centrivo.integration.config"
    _description = "Configurazione integrazioni (globale)"

    name = fields.Char(
        string="Nome", default="Configurazione integrazioni", readonly=True)

    # Il dominio ammette SOLO campi che hanno senso come sorgente vettore:
    #   - model = stock.picking (il campo vive sul trasferimento);
    #   - ttype = many2one (relazionale, non testuale);
    #   - relation che contiene "carrier" → ammette delivery.carrier (nativo)
    #     e qualunque modello-corriere di terzi (es. transport.carrier) in modo
    #     dinamico: se quel modulo non è installato, nessun campo ha quella
    #     relation e semplicemente non compare nella tendina.
    # Così non si può scegliere per errore un campo testuale come
    # carrier_tracking_ref, che è il NUMERO di spedizione, non il vettore.
    carrier_source_field_id = fields.Many2one(
        "ir.model.fields", string="Campo vettore sul trasferimento",
        domain="[('model', '=', 'stock.picking'), ('ttype', '=', 'many2one'), "
               "('relation', 'like', 'carrier')]",
        ondelete="set null",
        help="Campo del trasferimento da cui leggere il vettore per il push "
             "spedizioni verso i marketplace. Vuoto = si usa il corriere "
             "nativo di Odoo (carrier_id). In produzione con il modulo "
             "spedizioni di Dinamiche Aziendali va impostato su "
             "'transport_carrier_id'.")
    carrier_source_field_name = fields.Char(
        string="Nome tecnico campo",
        related="carrier_source_field_id.name", store=True)

    @api.model
    def get_carrier_source_field_name(self):
        """Nome tecnico del campo sorgente vettore. Default: carrier_id."""
        # order="id" garantisce che, se per qualunque motivo esistesse più di
        # un record (il modello è pensato come singleton ma nulla lo impone a
        # livello di database), qui e in open_config() si scelga sempre lo
        # stesso: il più vecchio. Un eventuale duplicato resta quindi
        # innocuo, perché entrambi i metodi convergono sulla stessa riga.
        config = self.search([], order="id", limit=1)
        if config and config.carrier_source_field_name:
            return config.carrier_source_field_name
        return DEFAULT_CARRIER_SOURCE_FIELD

    @api.model
    def open_config(self):
        """Azione di menu: apre l'unico record, creandolo alla prima apertura."""
        # sudo(): l'accesso in creazione su questo modello è riservato agli
        # amministratori (base.group_system), ma il menu "Configurazione" è
        # visibile a qualunque utente interno. Senza sudo, il primo utente
        # non amministratore che apre il menu a record ancora assente
        # otterrebbe un AccessError invece del form. La ricerca e la
        # creazione avvengono quindi con i permessi elevati solo per
        # arrivare al record; il form si apre poi con i diritti reali
        # dell'utente, che restano di sola lettura per chi non è
        # amministratore (le regole di accesso non vengono allargate).
        config = self.sudo().search([], order="id", limit=1)
        if not config:
            config = self.sudo().create({})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": config.id,
            "view_mode": "form",
            "target": "current",
            "name": "Configurazione integrazioni",
        }
