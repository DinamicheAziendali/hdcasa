# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.carrier.source — a quale corriere appartiene un vettore.

È l'unica tabella che l'utente compila: "BRT 100 è BRT". Non conosce i
marketplace. Il vettore può essere il corriere nativo di Odoo o quello di un
modulo di terzi: per questo modello sono la stessa cosa, identificata da tre
chiavi durevoli (modello, id, nome per esteso) che restano leggibili anche se
il modulo di terzi viene disinstallato.

Nota sulla duplicazione: la tendina di scelta del vettore e le sue tre funzioni
di supporto sono volutamente COPIATE dal vecchio centrivo.carrier.map invece di
essere condivise. Quel modello uscirà di scena al rilascio successivo, e
condividere il codice fra un modello vivo e uno in dismissione avrebbe legato la
vita del primo alla morte del secondo. La copia sparisce insieme all'originale.
"""
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class CentrivoCarrierSource(models.Model):
    _name = "centrivo.carrier.source"
    _description = "Vettore → Corriere"
    _order = "source_display"

    source_model = fields.Char(string="Modello sorgente", index=True)
    source_res_id = fields.Integer(string="ID sorgente", index=True)
    source_display = fields.Char(string="Vettore")
    source_record_key = fields.Selection(
        selection="_selection_source_records", string="Seleziona vettore",
        store=False, compute="_compute_source_record_key",
        inverse="_inverse_source_record_key",
        help="Vettore da collegare. La tendina elenca i record del modello "
             "puntato dal campo configurato in Integrations → Configurazione.")
    brand_id = fields.Many2one(
        "centrivo.carrier.brand", string="Corriere", required=True,
        ondelete="restrict")
    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True,
        default=lambda self: self.env.company, index=True)

    _sql_constraints = [
        ("carrier_source_uniq",
         "unique(source_model, source_res_id, company_id)",
         "Questo vettore è già collegato a un corriere per questa azienda."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        """Crea le righe e forza subito il controllo sul vettore.

        `@api.constrains` viene valutato da Odoo solo per i campi presenti nei
        vals passati al create: un create che non porta le chiavi durevoli
        (import, file di dati, un altro modulo) non farebbe scattare
        `_check_source` da solo, e una riga col solo corriere entrerebbe in
        tabella. Qui il controllo si invoca esplicitamente sui record appena
        creati, qualunque cosa ci fosse nei vals. Stesso schema del modello
        gemello centrivo.carrier.map.
        """
        records = super().create(vals_list)
        records._check_source()
        return records

    @api.constrains("source_model", "source_res_id")
    def _check_source(self):
        """Una riga senza vettore non serve a nulla e fa danni.

        Senza questo controllo si potrebbe salvare il solo corriere:
        `resolve_brand` non troverebbe mai quella riga (cerca per modello e id
        del vettore), il vincolo di unicità non intercetterebbe i duplicati
        (in SQL i NULL non collidono mai fra loro) e la schermata di Copertura
        corrieri mostrerebbe quel corriere come coperto quando non lo è.
        """
        for record in self:
            if not (record.source_model and record.source_res_id):
                raise ValidationError(
                    "Indica il vettore da collegare: seleziona un vettore "
                    "nella tendina 'Seleziona vettore'.")

    @api.model
    def _get_source_model(self):
        """Modello puntato dal campo vettore configurato (ripiego: nativo)."""
        name = self.env["centrivo.integration.config"].get_carrier_source_field_name()
        field = self.env["stock.picking"]._fields.get(name)
        if field is not None and field.type == "many2one":
            return field.comodel_name
        return "delivery.carrier"

    @api.model
    def _selection_source_records(self):
        """Opzioni della tendina: i record del modello sorgente."""
        model = self._get_source_model()
        if model not in self.env:
            return []
        records = self.env[model].sudo().search([], limit=1000)
        return sorted(
            [(str(rec.id), rec.display_name or ("#%s" % rec.id))
             for rec in records],
            key=lambda opzione: opzione[1])

    @api.depends("source_res_id", "source_model")
    def _compute_source_record_key(self):
        """Mostra la scelta SOLO se il modello sorgente combacia con l'attuale.

        Un id nudo come "7" individua vettori diversi in modelli diversi: se il
        campo sorgente configurato cambia, la tendina resta vuota e l'utente è
        costretto a ri-selezionare, invece di vedersi ripuntare la riga su un
        vettore che non ha mai scelto.
        """
        current_model = self._get_source_model()
        for record in self:
            if record.source_res_id and record.source_model == current_model:
                record.source_record_key = str(record.source_res_id)
            else:
                record.source_record_key = False

    def _inverse_source_record_key(self):
        """Dalla scelta in tendina alle tre chiavi durevoli.

        Le tre chiavi si scrivono con UNA sola write, non con tre assegnazioni
        separate. In Odoo ogni assegnazione su un record già salvato è una
        write() a sé, e ogni write() rivaluta i vincoli del modello: scrivendo
        prima `source_model` da solo, `_check_source` trovava `source_res_id`
        ancora vuoto e bocciava una riga che sarebbe stata completa la riga
        dopo. Era il caso di chi collega un vettore NUOVO dalla schermata
        «Vettori» — l'unica strada che ha un cliente — e si sentiva chiedere
        proprio il vettore che aveva appena scelto in tendina.
        """
        model = self._get_source_model()
        for record in self:
            if not record.source_record_key:
                continue
            if (record.source_res_id and record.source_model == model
                    and str(record.source_res_id) == record.source_record_key):
                continue
            res_id = int(record.source_record_key)
            source = self.env[model].sudo().browse(res_id).exists()
            if not source:
                continue
            record.write({
                "source_model": model,
                "source_res_id": res_id,
                "source_display": source.display_name or ("#%s" % res_id),
            })

    @api.model
    def resolve_brand(self, source_model, source_res_id, company):
        """Collegamento per quel vettore e azienda (recordset vuoto se assente)."""
        if not source_model or not source_res_id:
            return self.browse()
        return self.search([
            ("source_model", "=", source_model),
            ("source_res_id", "=", source_res_id),
            ("company_id", "=", company.id),
        ], limit=1)
