# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.shipment.status.map — normalizzazione stati corriere → stato Centrivo.

Tabella `(tracker_code, raw_code) → stato Centrivo`. Le righe sono SEMINATE da
ciascun adattatore (XML noupdate) ma restano MODIFICABILI da UI: un nuovo codice
corriere si gestisce senza rilasciare codice. Un codice non mappato NON rompe il
polling: si risolve in 'sconosciuto' e si logga su centrivo.shipment.log (log
interno del tracking) per scoprire il buco e mapparlo se diventa frequente.

Filosofia (Angelo): si mappano i codici FREQUENTI e CONOSCIUTI; gli altri vanno in
sconosciuto e si gestiscono a mano. Pattern di riferimento: OCA delivery_state
(studiato come reference, NON dipendenza hard).

Catalogo globale di configurazione (come gli stati): senza company_id.
"""
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CentrivoShipmentStatusMap(models.Model):
    _name = "centrivo.shipment.status.map"
    _description = "Mappatura stato corriere → stato Centrivo"
    _order = "tracker_code, raw_code"

    tracker_code = fields.Char(
        string="Corriere (tracker)", required=True, index=True,
        help="Codice del connettore di tracking (es. gls, brt, poste).")
    raw_code = fields.Char(
        string="Codice grezzo corriere", required=True, index=True,
        help="Codice di stato così come restituito dal corriere (es. INTRANSIT). "
             "Il confronto è case-insensitive (memorizzato in maiuscolo).")
    status_id = fields.Many2one(
        "centrivo.shipment.status", string="Stato Centrivo", ondelete="restrict",
        help="Stato Centrivo a cui mappare il codice grezzo. VUOTO = riga 'da "
             "mappare' (creata dall'auto-discovery): l'evento resta 'sconosciuto' "
             "finché non viene assegnato uno stato.")
    is_pre_advice = fields.Boolean(
        string="Pre-advice (solo dati)", default=False,
        help="Marca un codice grezzo che è SOLO trasmissione dati/pre-avviso, NON "
             "movimentazione reale (es. BRT 700, GLS PREADVICE). La 'prima lettura "
             "effettiva' di una spedizione è il primo evento la cui riga di "
             "mappatura ha questo flag a FALSE: i pre-advice non contano come inizio "
             "del conteggio SLA. È a livello di RIGA di mappatura, non di stato.")
    to_map = fields.Boolean(
        string="Da mappare", compute="_compute_to_map", store=True,
        help="Riga in attesa di assegnazione dello stato Centrivo (auto-discovery).")
    description = fields.Char(
        string="Note", help="Descrizione/promemoria del codice grezzo.")

    @api.depends("status_id")
    def _compute_to_map(self):
        for rec in self:
            rec.to_map = not rec.status_id

    _sql_constraints = [
        ("uniq_tracker_raw", "unique(tracker_code, raw_code)",
         "Esiste già una mappatura per questo corriere e codice grezzo."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._normalize_keys(dict(v)) for v in vals_list])

    def write(self, vals):
        return super().write(self._normalize_keys(dict(vals)))

    @staticmethod
    def _normalize_keys(vals):
        """Normalizza tracker_code/raw_code (strip + maiuscolo del raw_code)."""
        if vals.get("tracker_code"):
            vals["tracker_code"] = vals["tracker_code"].strip().lower()
        if vals.get("raw_code"):
            vals["raw_code"] = vals["raw_code"].strip().upper()
        return vals

    # ------------------------------------------------------------------
    # Normalizzazione (chiamata dal polling)
    # ------------------------------------------------------------------
    @api.model
    def _get_row(self, tracker_code, raw_code):
        """Riga di mappatura per (tracker_code, raw_code) — recordset vuoto se assente."""
        if not raw_code:
            return self.browse()
        return self.search([
            ("tracker_code", "=", (tracker_code or "").strip().lower()),
            ("raw_code", "=", raw_code.strip().upper()),
        ], limit=1)

    @api.model
    def normalize(self, tracker_code, raw_code, company=None,
                  raw_code_fallback=None, raw_description=None):
        """Traduce un codice grezzo del corriere nello stato Centrivo.

        Normalizzazione a DUE livelli (TASK_68), pensata per Poste ma generale:
          1) prova il codice PRIMARIO `raw_code` (per Poste = `status`, granulare);
          2) se il primario non è mappato a uno stato, prova il `raw_code_fallback`
             (per Poste = `phase`, base stabile): se questo risolve, lo usa MA
             registra comunque il primario come riga 'da mappare' (auto-discovery),
             così l'utente può raffinarlo senza perdere lo stato sensato nel frattempo;
          3) se nessuno dei due risolve → 'sconosciuto' + auto-discovery sul primario.

        Casi sul singolo codice (spec §3.5, nodo §11.5): riga con status_id → quello
        stato; riga "da mappare" (status_id NULLO) → trattata come riga assente (non
        risolve, non duplica nulla); nessuna riga → auto-discovery. `raw_description`
        (se passata) finisce nella riga 'da mappare' come testo leggibile, così chi la
        mappa capisce di cosa si tratta. Mai un'eccezione propagata.

        Retrocompatibile: senza `raw_code_fallback` il comportamento è identico a prima
        (GLS/BRT, che mappano un solo codice, non sono toccati).
        """
        Status = self.env["centrivo.shipment.status"]
        unknown = Status._get_by_code("unknown")
        tracker_code = (tracker_code or "").strip().lower()
        # 1) Codice primario (per Poste = status granulare).
        primary = self._get_row(tracker_code, raw_code) if raw_code else self.browse()
        if primary and primary.status_id:
            return primary.status_id
        raw = raw_code.strip().upper() if raw_code else None
        # 2) Fallback (per Poste = phase): usato solo se il primario non si risolve.
        if raw_code_fallback:
            fb = self._get_row(tracker_code, raw_code_fallback)
            if fb and fb.status_id:
                # Il primario esiste come codice ma NON è mappato: registralo come 'da
                # mappare' (solo se non c'è già una riga) e intanto usa il fallback.
                if raw and not primary:
                    self._ensure_unmapped_row(tracker_code, raw, raw_description)
                    self._log_unmapped(tracker_code, raw, company)
                return fb.status_id
        # 3) Nessuno dei due risolve → auto-discovery sul primario + 'sconosciuto'.
        if raw and not primary:
            self._ensure_unmapped_row(tracker_code, raw, raw_description)
            self._log_unmapped(tracker_code, raw, company)
        return unknown

    @api.model
    def _ensure_unmapped_row(self, tracker_code, raw_code, description=None):
        """Crea (idempotente) la riga 'da mappare' per un codice grezzo sconosciuto.

        Una sola riga per (tracker_code, raw_code): se esiste già — anche se l'utente
        l'ha completata assegnando uno stato — NON si tocca. status_id NULLO,
        is_pre_advice False. Se `description` è fornita (la descrizione testuale del
        codice grezzo: StatusDescription per Poste, descrizione evento per BRT) viene
        salvata nella riga così chi la mappa capisce di cosa si tratta. Create regolare
        via modello (mai SQL), in sudo perché il polling gira come utente normale e il
        catalogo è in sola lettura per loro.
        """
        if not tracker_code or not raw_code:
            return self.browse()
        if self.search_count([
            ("tracker_code", "=", tracker_code), ("raw_code", "=", raw_code),
        ]):
            return self.browse()
        desc = (description or "").strip()
        note = ("Da mappare (auto-discovery): %s" % desc) if desc \
            else "Da mappare (auto-discovery)."
        try:
            return self.sudo().create({
                "tracker_code": tracker_code,
                "raw_code": raw_code,
                "status_id": False,
                "is_pre_advice": False,
                "description": note,
            })
        except Exception:  # noqa: BLE001 - l'auto-discovery non deve rompere il polling
            _logger.warning("Auto-discovery riga 'da mappare' fallita: %s/%s",
                            tracker_code, raw_code)
            return self.browse()

    @api.model
    def _log_unmapped(self, tracker_code, raw_code, company):
        """Registra su centrivo.shipment.log un codice non mappato (→ sconosciuto)."""
        company = company or self.env.company
        try:
            self.env["centrivo.shipment.log"].create({
                "tracker_code": tracker_code or False,
                "operation": "normalize_status",
                "result": "skip",
                "message": "Codice tracking non mappato: %s/%s → sconosciuto. "
                           "Aggiungi una riga in 'Normalizzazione stati' se "
                           "diventa frequente." % (tracker_code, raw_code),
                "company_id": company.id,
            })
        except Exception:  # noqa: BLE001 - il logging non deve mai rompere il polling
            _logger.warning("Codice tracking non mappato: %s/%s (log fallito)",
                            tracker_code, raw_code)
