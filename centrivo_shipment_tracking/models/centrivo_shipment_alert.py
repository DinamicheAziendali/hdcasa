# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.alert — alert attivo su una spedizione (spec §3.4, §5, §6).

Un record per alert. Due nature (alert_kind):
  - threshold : superata una soglia temporale SLA (rule_id valorizzato);
  - status    : ingresso in uno stato problematico (trigger_status_id valorizzato).

ANTI-DUPLICATO: un solo alert APERTO per (shipment, alert_type). L'alert si RISOLVE
automaticamente quando la condizione decade (consegnato, uscito da giacenza, nuovo
evento entro la finestra di staleness, ecc.).

Alla generazione di un alert viene creata un'attività (mail.activity) agli utenti
fissi configurati: nella regola SLA (threshold) o nelle impostazioni globali SLA
(trigger su stato). Vedi centrivo.shipment._evaluate_sla per il motore.

Modello transazionale con company_id + record rule.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Etichette leggibili dei tipi di soglia (per name/attività). I trigger su stato
# usano invece il nome dello stato.
THRESHOLD_LABELS = {
    "missed_pickup": "Mancata presa in carico",
    "late_delivery": "Consegna oltre tempo atteso",
    "staleness": "Mancato aggiornamento",
}

# Tipo di attività nativo usato per le notifiche (To-Do).
ACTIVITY_TODO_XMLID = "mail.mail_activity_data_todo"


class CentrivoShipmentAlert(models.Model):
    _name = "centrivo.shipment.alert"
    _description = "Alert spedizione (SLA / stato)"
    _order = "trigger_date desc, id desc"

    name = fields.Char(string="Alert", compute="_compute_name", store=True)
    shipment_id = fields.Many2one(
        "centrivo.shipment", string="Spedizione", required=True,
        ondelete="cascade", index=True)

    alert_kind = fields.Selection(
        selection=[("threshold", "Soglia temporale"), ("status", "Stato problematico")],
        string="Natura", required=True, index=True)
    # Chiave di anti-duplicato e di filtro: per le soglie è il threshold_type, per i
    # trigger di stato è "status_<code>".
    alert_type = fields.Char(string="Tipo", required=True, index=True)

    threshold_type = fields.Selection(
        selection=[
            ("missed_pickup", "Mancata presa in carico effettiva"),
            ("late_delivery", "Consegna oltre tempo atteso"),
            ("staleness", "Mancato aggiornamento (staleness)"),
        ],
        string="Tipo soglia")
    rule_id = fields.Many2one(
        "centrivo.sla.rule", string="Regola SLA", ondelete="set null")
    trigger_status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato che ha fatto scattare l'alert",
        ondelete="set null")

    trigger_date = fields.Datetime(
        string="Scattato il", default=fields.Datetime.now, copy=False)
    resolved_date = fields.Datetime(string="Risolto il", copy=False)
    # Snooze a tempo: valorizzato SOLO dalle azioni manuali (Risolvi/Ignora). Entro
    # questa data la stessa condizione NON riapre l'alert; oltre, se la condizione
    # persiste, l'alert torna (snooze puro a tempo). Le auto-risoluzioni (condizione
    # decaduta) lo lasciano vuoto, così una recidiva successiva rigenera l'alert.
    snooze_until = fields.Datetime(
        string="Silenziato fino a", copy=False,
        help="Fin quando l'alert resta silenziato dopo un «Risolvi»/«Ignora» "
             "manuale. Entro questa data la stessa condizione non lo riapre; oltre, "
             "se la condizione è ancora attiva, l'alert torna. Vuoto per le "
             "auto-risoluzioni (condizione rientrata).")
    state = fields.Selection(
        selection=[
            ("open", "Aperto"),
            ("resolved", "Risolto"),
            ("ignored", "Ignorato"),
        ],
        string="Stato", default="open", required=True, index=True, copy=False)
    suggested_claim = fields.Boolean(
        string="Suggerisce reclamo",
        help="Ereditato dalla regola/trigger: marca la spedizione come candidata a "
             "reclamo (ponte Fase 3).")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    @api.depends("alert_kind", "alert_type", "threshold_type", "trigger_status_id",
                 "shipment_id.name")
    def _compute_name(self):
        for alert in self:
            alert.name = "%s · %s" % (
                alert._type_label(), alert.shipment_id.name or "")

    def _type_label(self):
        """Etichetta leggibile del tipo di alert."""
        self.ensure_one()
        if self.alert_kind == "status":
            return self.trigger_status_id.name or "Stato problematico"
        return THRESHOLD_LABELS.get(self.threshold_type or self.alert_type,
                                    self.alert_type or "Alert")

    # ==================================================================
    # Apertura / risoluzione (chiamate dal motore SLA)
    # ==================================================================
    @api.model
    def _open_alert(self, shipment, alert_type, kind, threshold_type=False,
                    rule=False, trigger_status=False, suggested_claim=False):
        """Apre un alert se non già attivo per (shipment, alert_type). Idempotente.

        Due livelli di anti-duplicato:
          1) esiste già un alert APERTO dello stesso tipo → idempotenza;
          2) esiste un alert gestito a mano (Risolvi/Ignora) ancora entro la finestra
             di SNOOZE (snooze_until nel futuro) → la riapertura è soppressa anche se
             la condizione è ancora vera. Scaduto lo snooze, o per un alert
             auto-risolto (snooze_until vuoto), la riapertura procede normalmente.
        L'attività agli utenti è creata SOLO alla prima apertura: l'anti-duplicato
        evita attività ripetute a ogni passaggio del motore.
        """
        now = fields.Datetime.now()
        existing_open = self.search([
            ("shipment_id", "=", shipment.id),
            ("alert_type", "=", alert_type),
            ("state", "=", "open"),
        ], limit=1)
        if existing_open:
            return existing_open
        snoozed = self.search([
            ("shipment_id", "=", shipment.id),
            ("alert_type", "=", alert_type),
            ("state", "in", ("resolved", "ignored")),
            ("snooze_until", "!=", False),
            ("snooze_until", ">", now),
        ], limit=1)
        if snoozed:
            return snoozed
        claim = bool(suggested_claim or (rule.suggest_claim if rule else False))
        alert = self.create({
            "shipment_id": shipment.id,
            "alert_kind": kind,
            "alert_type": alert_type,
            "threshold_type": threshold_type or False,
            "rule_id": rule.id if rule else False,
            "trigger_status_id": trigger_status.id if trigger_status else False,
            "suggested_claim": claim,
            "state": "open",
            "company_id": shipment.company_id.id,
        })
        alert._notify_users(rule)
        shipment._log("sla_alert", "ok", "Alert aperto: %s." % alert.name)
        return alert

    @api.model
    def _resolve_open(self, shipment, alert_type):
        """Risolve gli alert attivi (aperti/ignorati) di un tipo: la condizione è decaduta."""
        alerts = self.search([
            ("shipment_id", "=", shipment.id),
            ("alert_type", "=", alert_type),
            ("state", "in", ("open", "ignored")),
        ])
        alerts._do_resolve()

    def _do_resolve(self):
        """Marca come risolti gli alert attivi del recordset (+ log)."""
        now = fields.Datetime.now()
        for alert in self.filtered(lambda a: a.state in ("open", "ignored")):
            alert.write({"state": "resolved", "resolved_date": now})
            alert._close_activities("Alert SLA rientrato.")
            alert.shipment_id._log("sla_resolve", "ok",
                                   "Alert risolto: %s." % alert.name)

    def _close_activities(self, feedback):
        """Chiude le attività (mail.activity) generate da questo alert sulla spedizione.

        Le attività vengono create in _notify_users con `summary == self.name`: usiamo
        quel marcatore (più res_model/res_id) per identificare SOLO le notifiche del
        sistema SLA, senza toccare eventuali attività manuali aggiunte dall'utente sulla
        stessa spedizione. `action_feedback` le segna come fatte: spariscono dalle "Mie
        attività" pendenti e restano tracciate come "fatte" nella chatter (storico utile
        per i reclami). sudo: l'attività può essere assegnata a un utente diverso da chi
        risolve l'alert, ma va chiusa comunque.
        """
        self.ensure_one()
        activities = self.env["mail.activity"].sudo().search([
            ("res_model", "=", "centrivo.shipment"),
            ("res_id", "=", self.shipment_id.id),
            ("summary", "=", self.name),
        ])
        if activities:
            activities.action_feedback(feedback=feedback)

    def _notify_users(self, rule=False):
        """Crea l'attività (mail.activity) agli utenti fissi (spec §6.3).

        threshold → utenti della regola SLA; status → utenti di default delle
        impostazioni globali SLA. Usa il meccanismo nativo activity_schedule (un
        record mail.activity per utente). Nessun loop di invio manuale.
        """
        self.ensure_one()
        if self.alert_kind == "threshold" and rule:
            users = rule.user_ids
        else:
            users = self.env["centrivo.tracking.config"]._get_alert_users()
        if not users:
            return
        try:
            act_type = self.env.ref(ACTIVITY_TODO_XMLID)
        except ValueError:
            act_type = self.env["mail.activity.type"].browse()
        note = ("Alert di tracking: %s.%s" % (
            self.name,
            " Valutare l'apertura di un reclamo." if self.suggested_claim else ""))
        for user in users:
            self.shipment_id.activity_schedule(
                act_type_xmlid=ACTIVITY_TODO_XMLID if act_type else False,
                summary=self.name,
                note=note,
                user_id=user.id,
            )

    # ==================================================================
    # Azioni utente
    # ==================================================================
    def action_resolve(self):
        """Bottone/azione: gestione manuale "Risolvi" (stato risolto + snooze)."""
        self._handle_manual("resolved", "Alert SLA gestito (risolto).")
        return True

    def action_ignore(self):
        """Bottone/azione: gestione manuale "Ignora" (stato ignorato + snooze).

        Stessa soppressione di "Risolvi" (snooze a tempo, identica durata): cambia
        solo l'etichetta di stato registrata (ignored vs resolved) per l'analitica.
        """
        self._handle_manual("ignored", "Alert SLA ignorato.")
        return True

    def _handle_manual(self, new_state, feedback):
        """Gestione manuale di un alert aperto: stato finale + SNOOZE a tempo.

        Sia "Risolvi" sia "Ignora" mettono l'alert in snooze per la durata globale
        configurata (ore LAVORATIVE, weekend esclusi): entro quella finestra la
        stessa condizione non riapre l'alert; scaduta, se la condizione persiste,
        l'alert torna. Chiude anche l'attività (mail.activity) collegata. Opera solo
        sugli alert aperti (i pulsanti sono visibili solo in stato 'open').
        """
        Shipment = self.env["centrivo.shipment"]
        now = fields.Datetime.now()
        hours = self.env["centrivo.tracking.config"]._get_snooze_hours()
        deadline = Shipment._add_working_hours(now, hours)
        log_op = "sla_resolve" if new_state == "resolved" else "sla_ignore"
        verb = "risolto" if new_state == "resolved" else "ignorato"
        for alert in self.filtered(lambda a: a.state == "open"):
            alert.write({
                "state": new_state,
                "resolved_date": now,
                "snooze_until": deadline,
            })
            alert._close_activities(feedback)
            alert.shipment_id._log(
                log_op, "ok",
                "Alert %s con snooze fino al %s: %s." % (verb, deadline, alert.name))
