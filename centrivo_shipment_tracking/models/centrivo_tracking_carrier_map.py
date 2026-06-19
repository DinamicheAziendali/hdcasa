# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.tracking.carrier.map — mappa "record corriere sorgente" → adattatore.

Livello 2 della risoluzione corriere (vedi centrivo.tracking.config per il Livello
1, cioè DA QUALE campo del picking leggere il corriere). Qui si associa il SINGOLO
record corriere (il valore del campo sorgente — es. un delivery.carrier nativo, o
un transport.carrier di Dinamiche Aziendali, o un centrivo.carrier) all'adattatore
di tracking (tracker_code: gls/brt/poste). Molti-a-uno: più corrieri → stesso
adattatore (es. BRT, BRT 100, DPD → brt).

WIDGET DI SELEZIONE (TASK_43 — riscritto da zero):
Il corriere si sceglie con un campo **Selection popolato dinamicamente** dai record
del modello sorgente configurato (`source_selection`, valori = `(str(id),
display_name)`). Un valore Selection è uno SCALARE che il web client invia sempre,
in modo affidabile, nei vals di create/write — a differenza del campo Reference a
comodel variabile usato prima (TASK_40/42), il cui valore non si propagava in modo
affidabile (trappola computed+inverse). Niente più Reference dinamico.

CHIAVI DUREVOLI (base di verità, invariata): `source_model` + `source_res_id` +
`source_display`. Vengono compilate dalla selezione in create/write PRIMA del
constraint, e sopravvivono alla disinstallazione del modulo di terzi (la risoluzione
runtime `resolve_tracker` usa SOLO queste).
"""
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Limite di sicurezza sul numero di corrieri elencati nella Selection (i corrieri
# sono pochi; il cap evita liste assurde se il modello sorgente fosse enorme).
SOURCE_SELECTION_LIMIT = 1000


class CentrivoTrackingCarrierMap(models.Model):
    _name = "centrivo.tracking.carrier.map"
    _description = "Mappa corriere sorgente → adattatore di tracking"
    _order = "source_display, id"

    # --- Adattatore di destinazione ---------------------------------------
    tracker_code = fields.Selection(
        selection="_selection_tracker_code", string="Adattatore tracking",
        required=True,
        help="Connettore di tracking a cui mappa questo corriere (es. GLS). Più "
             "corrieri possono puntare allo stesso adattatore.")

    # --- Widget di selezione corriere (Selection dinamica, valore = str(id)) ---
    source_selection = fields.Selection(
        selection="_selection_source_records", string="Seleziona corriere",
        required=True,
        help="Corriere da mappare. La lista è popolata dai record del modello "
             "puntato dal campo sorgente (Impostazioni tracking): di norma i "
             "metodi di consegna nativi.")

    # --- Chiavi DUREVOLI del valore corriere sorgente ----------------------
    # Non required a livello di campo (sono riempite in create/write dalla
    # selezione, PRIMA del constraint): l'integrità è garantita da _check_source.
    source_model = fields.Char(
        string="Modello sorgente", index=True,
        help="Modello tecnico del record corriere (es. delivery.carrier).")
    source_res_id = fields.Integer(
        string="ID record sorgente", index=True)
    source_display = fields.Char(
        string="Corriere sorgente",
        help="Etichetta del record corriere (memorizzata: resta leggibile anche "
             "se il modulo che definisce il corriere viene disinstallato).")

    company_id = fields.Many2one(
        "res.company", string="Azienda", required=True, index=True,
        default=lambda self: self.env.company)

    _sql_constraints = [
        ("uniq_source_company",
         "unique(source_model, source_res_id, company_id)",
         "Esiste già una mappatura per questo corriere su questa azienda."),
    ]

    # ------------------------------------------------------------------
    # Selezioni dinamiche
    # ------------------------------------------------------------------
    @api.model
    def _selection_tracker_code(self):
        from ..connectors.base import TrackingConnector
        options = TrackingConnector.get_selection()
        return options or [("none", "Nessun adattatore installato")]

    @api.model
    def _get_source_model(self):
        """Modello da cui pescare i corrieri = comodel del campo sorgente configurato.

        - campo sorgente nativo `carrier_id` → `delivery.carrier` (default/standard);
        - `transport_carrier_id` → `transport.carrier`, SOLO se installato;
        - se il comodel non è risolvibile (campo non relazionale, o modello assente
          su Community puro) → degrada al nativo `delivery.carrier`.
        Niente dipendenza hard: il modello di terzi è letto dinamico/opzionale.
        """
        relation = self.env["centrivo.tracking.config"].get_source_relation()
        if relation and relation in self.env:
            return relation
        return "delivery.carrier"

    @api.model
    def _selection_source_records(self):
        """Opzioni del Selection 'Seleziona corriere' = i record del modello sorgente.

        Valori: (str(id), display_name). Ordinati per etichetta. Se il modello non
        è disponibile o non ha record, ritorna lista vuota (nessun crash).
        """
        model = self._get_source_model()
        if model not in self.env:
            return []
        records = self.env[model].sudo().search([], limit=SOURCE_SELECTION_LIMIT)
        options = [(str(rec.id), rec.display_name or ("#%s" % rec.id))
                   for rec in records]
        options.sort(key=lambda opt: (opt[1] or "").lower())
        return options

    # ------------------------------------------------------------------
    # Selezione → chiavi durevoli
    # ------------------------------------------------------------------
    @api.onchange("source_selection")
    def _onchange_source_selection(self):
        """UX: alla scelta del corriere, compila subito le chiavi durevoli (visibili
        come valorizzate dietro le quinte; arrivano comunque nei vals del save)."""
        for rec in self:
            model, res_id, display = rec._resolve_selection(rec.source_selection)
            if res_id:
                rec.source_model = model
                rec.source_res_id = res_id
                rec.source_display = display

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_source_selection(vals)
        return super().create(vals_list)

    def write(self, vals):
        self._apply_source_selection(vals)
        return super().write(vals)

    def _apply_source_selection(self, vals):
        """Compila le chiavi durevoli in `vals` dalla selezione, PRIMA del constraint.

        Il valore del Selection (str dell'id) è uno scalare presente in modo
        affidabile nei vals inviati dal web client: lo traduciamo qui in
        (source_model, source_res_id, source_display) leggendo il modello sorgente
        configurato. `source_selection` resta nei vals (campo stored): così il form
        ricarica la scelta fatta.
        """
        sel = vals.get("source_selection")
        if not sel:
            return
        model, res_id, display = self._resolve_selection(sel)
        if not res_id:
            return
        vals["source_model"] = model
        vals["source_res_id"] = res_id
        vals["source_display"] = display

    def _resolve_selection(self, selection_value):
        """Traduce il valore Selection (str id) in (model, res_id, display).

        Ritorna (model, 0, '') se il valore è vuoto/non valido. L'etichetta è quella
        del record se esiste, altrimenti un fallback "model,id" (mai un'eccezione se
        il modello non è installato)."""
        model = self._get_source_model()
        try:
            res_id = int(selection_value)
        except (TypeError, ValueError):
            return model, 0, ""
        if not res_id:
            return model, 0, ""
        display = "%s,%s" % (model, res_id)
        if model in self.env:
            record = self.env[model].browse(res_id)
            if record.exists():
                display = record.display_name or display
        return model, res_id, display

    @api.constrains("source_model", "source_res_id", "source_display")
    def _check_source(self):
        """Integrità: il corriere sorgente deve essere identificato (modello+id+etichetta)."""
        for rec in self:
            if not (rec.source_model and rec.source_res_id and rec.source_display):
                raise ValidationError(
                    "Seleziona un corriere nel campo 'Seleziona corriere'.")

    # ------------------------------------------------------------------
    # Risoluzione (chiamata dal picking) — INVARIATA
    # ------------------------------------------------------------------
    @api.model
    def resolve_tracker(self, source_model, source_res_id, company):
        """Ritorna il tracker_code mappato per (modello, id, azienda), o ''.

        Cerca prima nella company indicata, poi tra le righe senza company (globali).
        """
        if not source_model or not source_res_id:
            return ""
        # source_res_id può arrivare come int (id del record): normalizziamo a int.
        try:
            source_res_id = int(source_res_id)
        except (TypeError, ValueError):
            return ""
        domain = [
            ("source_model", "=", source_model),
            ("source_res_id", "=", source_res_id),
        ]
        # company indicata → righe globali (company vuota) → qualunque company
        # visibile (rete di sicurezza mono-azienda; le record rule impediscono
        # comunque di vedere righe di altre aziende in multi-company).
        rec = self.search(domain + [("company_id", "=", company.id)], limit=1) \
            or self.search(domain + [("company_id", "=", False)], limit=1) \
            or self.search(domain, limit=1)
        return rec.tracker_code or ""
