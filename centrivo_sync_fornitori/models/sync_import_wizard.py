# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Wizard di import prodotti a MAPPATURA DINAMICA (Flusso A, spec v2.x).

L'operatore carica un file (CSV/XLSX) al momento, il wizard legge le INTESTAZIONI
REALI e propone la mappatura SALVATA del fornitore; l'operatore rivede destinazioni
e attivazioni PER QUESTO GIRO, poi importa applicando solo le colonne attive.
"""
import base64
import json
import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..connectors import catalog_parser as cp
from .sync_column_map import (
    DESTINATION_KIND, PRODUCT_FIELD_DOMAIN, SPECIAL_TARGETS)

_logger = logging.getLogger(__name__)


class CentrivoSyncImportWizard(models.TransientModel):
    _name = "centrivo.sync.import.wizard"
    _description = "Import prodotti fornitore (mappatura dinamica)"

    channel_id = fields.Many2one(
        "centrivo.sync.channel", string="Fornitore", required=True,
        ondelete="cascade")
    upload_file = fields.Binary(string="File (CSV o XLSX)", attachment=False)
    upload_filename = fields.Char(string="Nome file")
    line_ids = fields.One2many(
        "centrivo.sync.import.line", "wizard_id", string="Mappatura colonne")
    headers_loaded = fields.Boolean(string="Intestazioni lette", default=False)

    # ------------------------------------------------------------------
    def _parse_file(self):
        """Decodifica il file caricato e ritorna (headers, rows)."""
        self.ensure_one()
        if not self.upload_file:
            raise UserError(_("Carica prima un file CSV o XLSX."))
        try:
            content = base64.b64decode(self.upload_file)
        except Exception as exc:  # noqa: BLE001
            raise UserError(_("File non decodificabile: %s") % exc)
        try:
            return cp.read_table(content, self.upload_filename)
        except ValueError as exc:
            raise UserError(_("Lettura del file fallita: %s") % exc)

    def action_read_headers(self):
        """STEP 1: legge le intestazioni reali e propone la mappatura salvata."""
        self.ensure_one()
        headers, _rows = self._parse_file()
        headers = [h for h in headers if h]
        if not headers:
            raise UserError(_("Nessuna intestazione trovata nella prima riga."))

        saved = {m.source_header: m for m in self.channel_id.column_map_ids}
        self.line_ids.unlink()
        commands = []
        seq = 0
        seen = set()
        # Colonne presenti nel file: ri-propongono la mappatura salvata.
        for header in headers:
            seq += 10
            seen.add(header)
            existing = saved.get(header)
            if existing:
                commands.append((0, 0, {
                    "source_header": header,
                    "destination_kind": existing.destination_kind,
                    "special_target": existing.special_target,
                    "dest_field_id": existing.dest_field_id.id,
                    "active_in_run": existing.active_in_run,
                    "sequence": seq,
                    "is_missing": False,
                }))
            else:
                commands.append((0, 0, {
                    "source_header": header,
                    "destination_kind": "ignore",
                    "active_in_run": False,
                    "sequence": seq,
                    "is_missing": False,
                }))
        # Colonne salvate ma SPARITE dal file: segnalate, disattivate.
        for header, mapping in saved.items():
            if header in seen:
                continue
            seq += 10
            commands.append((0, 0, {
                "source_header": header,
                "destination_kind": mapping.destination_kind,
                "special_target": mapping.special_target,
                "dest_field_id": mapping.dest_field_id.id,
                "active_in_run": False,
                "sequence": seq,
                "is_missing": True,
            }))
        self.write({"line_ids": commands, "headers_loaded": True})
        return self._reopen()

    def action_save_mapping(self):
        """Salva la mappatura corrente sul fornitore (ri-proposta poi)."""
        self.ensure_one()
        self._save_mapping()
        return self._reopen()

    def _save_mapping(self):
        """Upsert delle righe (non mancanti) su channel.column_map_ids."""
        ColumnMap = self.env["centrivo.sync.column.map"]
        saved = {m.source_header: m for m in self.channel_id.column_map_ids}
        for line in self.line_ids:
            if line.is_missing:
                continue
            vals = {
                "destination_kind": line.destination_kind,
                "special_target": line.special_target
                if line.destination_kind == "special" else False,
                "dest_field_id": line.dest_field_id.id
                if line.destination_kind == "field" else False,
                "active_in_run": line.active_in_run,
                "sequence": line.sequence,
            }
            existing = saved.get(line.source_header)
            if existing:
                existing.write(vals)
            else:
                vals.update({
                    "channel_id": self.channel_id.id,
                    "source_header": line.source_header,
                })
                ColumnMap.create(vals)

    def _collect_active_specs(self):
        """Specifiche delle SOLE colonne attive, mappate e presenti nel file.

        Costruite dalle righe del wizard (niente ri-parsing del file): le colonne
        salvate ma assenti dal file hanno is_missing=True e vengono escluse.
        """
        specs = []
        for line in self.line_ids:
            if (not line.active_in_run or line.is_missing
                    or line.destination_kind == "ignore"):
                continue
            specs.append({
                "header": line.source_header,
                "kind": line.destination_kind,
                "special": line.special_target,
                "field_name": line.dest_field_id.name or False,
            })
        return specs

    def action_import(self):
        """Flusso A: ACCODA l'import in background (non esegue nella request).

        Persiste file + mappatura in un centrivo.sync.import.job e triggera il
        cron, restituendo subito il controllo all'operatore. L'esecuzione (senza
        limite di tempo della richiesta web) avviene in background a chunk.
        """
        self.ensure_one()
        if not self.upload_file:
            raise UserError(_("Carica prima un file CSV o XLSX."))
        if not self.headers_loaded or not self.line_ids:
            # Comodità: se l'utente non ha letto le intestazioni, le legge ora.
            self.action_read_headers()

        # Salva la mappatura aggiornata (così è ri-proposta al prossimo giro).
        self._save_mapping()

        specs = self._collect_active_specs()
        if not specs:
            raise UserError(_("Nessuna colonna attiva e mappata: niente da importare."))
        # Valida subito la presenza della colonna-chiave (codice fornitore),
        # così l'operatore ha feedback immediato prima di accodare.
        self.channel_id._key_header(specs)

        Job = self.env["centrivo.sync.import.job"]
        job = Job.create({
            "channel_id": self.channel_id.id,
            "upload_file": self.upload_file,
            "upload_filename": self.upload_filename,
            "spec_json": json.dumps(specs),
            "state": "queued",
        })
        Job._arm_cron()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import avviato in background"),
                "message": _(
                    "Import accodato (job #%s). Segui il progresso sul canale "
                    "(campo 'Stato ultimo import' e scheda 'Import in background') "
                    "e nel 'Log operazioni'. Puoi chiudere questa finestra."
                ) % job.id,
                "type": "success",
                "sticky": False,
            },
        }

    def _reopen(self):
        """Riapre il wizard sullo stesso record (per mostrare le righe aggiornate)."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Importa prodotti"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class CentrivoSyncImportLine(models.TransientModel):
    _name = "centrivo.sync.import.line"
    _description = "Riga di mappatura del wizard di import (per giro)"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "centrivo.sync.import.wizard", string="Wizard", required=True,
        ondelete="cascade")
    sequence = fields.Integer(string="Sequenza", default=10)
    source_header = fields.Char(string="Colonna file", readonly=True)
    destination_kind = fields.Selection(
        selection=DESTINATION_KIND, string="Tipo destinazione",
        required=True, default="ignore")
    special_target = fields.Selection(
        selection=SPECIAL_TARGETS, string="Destinazione speciale")
    dest_field_id = fields.Many2one(
        "ir.model.fields", string="Campo prodotto", domain=PRODUCT_FIELD_DOMAIN,
        ondelete="set null")
    active_in_run = fields.Boolean(string="Attiva", default=True)
    is_missing = fields.Boolean(
        string="Assente dal file", readonly=True,
        help="Colonna presente nella mappatura salvata ma non in questo file.")
