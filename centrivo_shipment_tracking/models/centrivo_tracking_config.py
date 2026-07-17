# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.tracking.config — configurazione globale del modulo tracking.

Singleton (un record). Tiene la scelta di DA QUALE CAMPO del picking il modulo
legge il corriere (Livello 1): un riferimento a un campo di stock.picking. Sul
campo reale di Angelo il corriere NON sta nel delivery.carrier nativo ma in un
campo di terzi (es. transport_carrier_id di Dinamiche Aziendali), che potrebbe
sparire se quel modulo viene disinstallato. Per questo:

  - il campo sorgente è CONFIGURABILE (non cablato);
  - viene letto in modo DINAMICO/OPZIONALE (vedi stock.picking._centrivo_source_carrier);
  - nessuna dipendenza hard da transport_carrier_id né da moduli di terzi.

Catalogo di configurazione GLOBALE (senza company_id): la scelta del campo è la
stessa per tutte le aziende dell'istanza.
"""
from odoo import api, fields, models

# Campo sorgente di default se la config non è impostata: il corriere nativo.
# Garantisce la non-regressione per chi usa delivery.carrier come sorgente.
DEFAULT_SOURCE_FIELD = "carrier_id"

# Durata di default dello snooze (ore lavorative) quando un operatore preme
# "Risolvi"/"Ignora" su un alert la cui condizione è ancora attiva. Usata se la
# config è assente o valorizzata a <= 0.
DEFAULT_SNOOZE_HOURS = 48.0


class CentrivoTrackingConfig(models.Model):
    _name = "centrivo.tracking.config"
    _description = "Configurazione tracking (globale)"

    name = fields.Char(string="Nome", default="Configurazione tracking", readonly=True)

    # Hardening (TASK_49): il dominio ammette SOLO campi che hanno senso come
    # sorgente corriere, così non è più possibile scegliere per errore un campo
    # testuale (es. carrier_tracking_ref, il NUMERO di spedizione). Condizioni:
    #   - model = stock.picking (il campo vive sul trasferimento);
    #   - ttype = many2one (deve essere relazionale, non Char/Selection/Text);
    #   - relation contiene "carrier" → ammette delivery.carrier (nativo, default)
    #     E qualsiasi modello-corriere di terzi (es. transport.carrier) in modo
    #     DINAMICO/OPZIONALE: se quel modulo non è installato nessun campo ha quella
    #     relation e semplicemente non compare (nessuna dipendenza hard).
    source_field_id = fields.Many2one(
        "ir.model.fields", string="Campo corriere sul trasferimento",
        domain="[('model', '=', 'stock.picking'), ('ttype', '=', 'many2one'), "
               "('relation', 'like', 'carrier')]",
        ondelete="set null",
        help="Campo Many2one di stock.picking da cui leggere il corriere "
             "(Livello 1). La tendina mostra SOLO i campi relazionali verso un "
             "modello-corriere: 'carrier_id' (nativo, default) ed eventuali campi "
             "di terzi come 'transport_carrier_id' (Dinamiche Aziendali). I campi "
             "testuali (es. il numero di spedizione 'carrier_tracking_ref') NON "
             "sono selezionabili. Il campo è letto in modo dinamico: se non esiste "
             "o è vuoto, si passa all'assegnazione manuale del corriere.")
    source_field_name = fields.Char(
        string="Nome tecnico campo", related="source_field_id.name", store=True)

    # --- Impostazioni globali SLA (spec §9) -------------------------------
    # Utenti di default a cui assegnare l'attività per gli alert da TRIGGER DI STATO
    # (gli alert da soglia usano invece gli utenti della singola regola SLA). La
    # frequenza del motore SLA si regola sul cron 'Tracking: valuta SLA' (UI), e
    # QUALI stati problematici generano alert si configura sugli Stati spedizione
    # (campo 'Genera alert all'ingresso').
    default_alert_user_ids = fields.Many2many(
        "res.users", "centrivo_tracking_config_alert_user_rel",
        "config_id", "user_id", string="Utenti notifica SLA (trigger di stato)",
        help="Utenti a cui assegnare l'attività quando una spedizione entra in uno "
             "stato problematico configurato per generare alert. Gli alert da soglia "
             "temporale usano gli utenti della relativa regola SLA.")

    # Snooze alert (spec §5): quando un operatore gestisce un alert ("Risolvi" o
    # "Ignora") la cui condizione è ancora attiva, l'alert viene silenziato per
    # questa durata invece di riaprirsi al polling/valutazione successiva. Ore
    # LAVORATIVE (weekend esclusi), coerente con le soglie SLA.
    alert_snooze_hours = fields.Float(
        string="Snooze alert (ore lavorative)", default=DEFAULT_SNOOZE_HOURS,
        help="Per quante ore LAVORATIVE (weekend esclusi) un alert resta silenziato "
             "dopo che un operatore preme «Risolvi» o «Ignora». Entro questa finestra "
             "la stessa condizione non riapre l'alert; scaduta, se la condizione "
             "persiste, l'alert torna. Vale sia per «Risolvi» sia per «Ignora». "
             "Le auto-risoluzioni (condizione rientrata) non usano snooze.")

    # --- Registrazione Centrivo License Server (FASE 1: best-effort) -------
    # Campi NON memorizzati: proxy verso gli ir.config_parameter del client licenza
    # (centrivo.license). Il contatto è opzionale; l'URL è di sola lettura qui
    # (modificabile da Impostazioni → Tecnico → Parametri di sistema).
    centrivo_contact = fields.Char(
        string="Contatto Centrivo",
        compute="_compute_license_params", inverse="_inverse_centrivo_contact",
        help="Riferimento OPZIONALE (email o nome) inviato al Centrivo License Server "
             "alla registrazione dell'istanza. Può restare vuoto: il ping parte "
             "comunque senza contatto.")
    centrivo_license_server_url = fields.Char(
        string="URL license server", compute="_compute_license_params", readonly=True,
        help="URL a cui il modulo invia il ping di registrazione. Modificabile da "
             "Impostazioni → Tecnico → Parametri di sistema "
             "(centrivo.license.server_url).")

    @api.depends_context("uid")
    def _compute_license_params(self):
        license_model = self.env["centrivo.license"]
        icp = self.env["ir.config_parameter"].sudo()
        contact = icp.get_param("centrivo.license.contact", "")
        url = license_model._get_server_url()
        for rec in self:
            rec.centrivo_contact = contact
            rec.centrivo_license_server_url = url

    def _inverse_centrivo_contact(self):
        icp = self.env["ir.config_parameter"].sudo()
        for rec in self:
            icp.set_param("centrivo.license.contact", rec.centrivo_contact or "")

    def action_register_license_now(self):
        """Bottone "Registra ora": re-invio manuale del ping (utile per test)."""
        self.ensure_one()
        self.env["centrivo.license"].action_register_now()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Registrazione Centrivo",
                "message": "Ping di registrazione inviato (best-effort). "
                           "Esito non bloccante: vedi i log a debug.",
                "type": "info",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    # Singleton helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_config(self):
        """Ritorna il record di configurazione (creandolo se assente)."""
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config

    @api.model
    def get_source_field_name(self):
        """Nome tecnico del campo sorgente corriere (default: carrier_id)."""
        config = self.search([], limit=1)
        return (config.source_field_name or DEFAULT_SOURCE_FIELD) \
            if config else DEFAULT_SOURCE_FIELD

    @api.model
    def get_source_relation(self):
        """Comodel del campo sorgente, se è relazionale e installato (altrimenti '')."""
        config = self.search([], limit=1)
        field = config.source_field_id if config else False
        if field and field.relation and field.relation in self.env:
            return field.relation
        return ""

    @api.model
    def _get_alert_users(self):
        """Utenti di default per gli alert da trigger di stato (impostazioni globali)."""
        config = self.search([], limit=1)
        return config.default_alert_user_ids if config else self.env["res.users"].browse()

    @api.model
    def _get_snooze_hours(self):
        """Durata snooze (ore lavorative) per «Risolvi»/«Ignora».

        Fallback al default se la config è assente o valorizzata a <= 0 (uno snooze
        nullo riproporrebbe subito l'alert, reintroducendo il problema).
        """
        config = self.search([], limit=1)
        hours = config.alert_snooze_hours if config else 0.0
        return hours if hours and hours > 0 else DEFAULT_SNOOZE_HOURS

    def action_open_config(self):
        """Apre il record di configurazione (usato dal menu)."""
        config = self._get_config()
        return {
            "type": "ir.actions.act_window",
            "name": "Configurazione tracking",
            "res_model": "centrivo.tracking.config",
            "res_id": config.id,
            "view_mode": "form",
            "target": "current",
        }
