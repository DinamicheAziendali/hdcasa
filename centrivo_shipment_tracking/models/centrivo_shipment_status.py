# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.status — vocabolario degli stati di tracking (sistema + utente).

Gli stati NON sono un Selection ma un MODELLO, perché l'utente deve poterne
aggiungere di propri (decisione di Angelo, vedi spec §3.2). Gli stati di SISTEMA
(is_system=True) sono protetti: non cancellabili, codice/flag non modificabili,
perché su di essi poggiano polling adattivo, aggregazione multicollo e (in futuro)
i trigger reclami. Gli stati UTENTE hanno comportamento NEUTRO di default
(cadenza polling standard, nessun trigger) finché l'admin non li configura.

Nota multi-azienda: questo è un CATALOGO di configurazione condiviso (come una
definizione di macchina a stati), quindi è GLOBALE (senza company_id). I dati
transazionali (spedizione/collo/evento/account) portano invece company_id +
record rule. Scelta documentata nel report.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

# Cadenza di polling di default (ore) per gli stati utente non configurati.
DEFAULT_USER_POLLING_HOURS = 12.0


class CentrivoShipmentStatus(models.Model):
    _name = "centrivo.shipment.status"
    _description = "Stato di tracking della spedizione"
    _order = "sequence, id"

    name = fields.Char(string="Stato", required=True, translate=True)
    # Codice tecnico stabile: usato dal codice (aggregazione, terminale, ecc.).
    code = fields.Char(
        string="Codice tecnico", required=True, index=True,
        help="Identificativo tecnico stabile dello stato (es. in_transit). "
             "Per gli stati di sistema è bloccato.")
    sequence = fields.Integer(string="Sequenza", default=10)
    active = fields.Boolean(string="Attivo", default=True)

    is_system = fields.Boolean(
        string="Stato di sistema", default=False, readonly=True, copy=False,
        help="Stati fissi su cui poggia la logica del modulo: non cancellabili "
             "né rinominabili nel codice. Gli stati aggiunti dall'utente hanno "
             "questo flag a False.")

    # --- Semantica usata dalla logica -------------------------------------
    is_terminal = fields.Boolean(
        string="Terminale (stop tracking)",
        help="Stato finale: la spedizione esce dal polling (consegnato, reso).")
    is_problematic = fields.Boolean(
        string="Problematico",
        help="Stato che richiede attenzione (giacenza, anomalia): in aggregazione "
             "multicollo prevale e (in futuro) può suggerire un reclamo.")
    is_partial = fields.Boolean(
        string="Consegna parziale",
        help="Alcuni colli consegnati, altri no: NON terminale, si continua a "
             "tracciare i colli mancanti.")
    aggregation_priority = fields.Integer(
        string="Priorità aggregazione", default=10,
        help="Quando i colli hanno stati diversi (e nessuno è 'consegnato'), "
             "vince lo stato con priorità più alta. Valore più alto = più "
             "prioritario (gli stati problematici hanno priorità maggiore).")

    # --- Polling adattivo --------------------------------------------------
    polling_hours = fields.Float(
        string="Cadenza polling (ore)", default=DEFAULT_USER_POLLING_HOURS,
        help="Ogni quante ore reinterrogare il corriere per le spedizioni in "
             "questo stato. 0 = nessun polling (stati terminali).")
    alert_if_persists = fields.Boolean(
        string="Alert se persiste",
        help="Predisposizione: se la spedizione resta troppo a lungo in questo "
             "stato, genera un alert (logica negli step successivi).")

    # --- Trigger su stato (spec §5) ---------------------------------------
    alert_on_entry = fields.Boolean(
        string="Genera alert all'ingresso",
        help="Se attivo, l'ingresso della spedizione in questo stato genera un "
             "alert immediato (senza timer). Tipicamente per gli stati problematici "
             "(giacenza, anomalia, reso).")
    alert_suggests_claim = fields.Boolean(
        string="L'alert suggerisce reclamo",
        help="Se attivo, l'alert da trigger di stato marca la spedizione come "
             "candidata a reclamo (ponte Fase 3).")

    _sql_constraints = [
        ("uniq_code", "unique(code)",
         "Esiste già uno stato con questo codice tecnico."),
    ]

    # ------------------------------------------------------------------
    # Protezione degli stati di sistema
    # ------------------------------------------------------------------
    def write(self, vals):
        """Impedisce di alterare codice/flag di sistema sugli stati protetti."""
        protected = {"code", "is_system"}
        if protected & set(vals):
            for status in self:
                if status.is_system:
                    raise UserError(
                        "Lo stato di sistema '%s' non può cambiare codice o "
                        "perdere il flag di sistema." % status.name)
        return super().write(vals)

    def unlink(self):
        """Impedisce la cancellazione degli stati di sistema."""
        for status in self:
            if status.is_system:
                raise UserError(
                    "Lo stato di sistema '%s' non può essere eliminato." % status.name)
        return super().unlink()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------
    @api.model
    def _get_by_code(self, code):
        """Ritorna lo stato con quel codice (recordset vuoto se assente)."""
        return self.search([("code", "=", code)], limit=1)
