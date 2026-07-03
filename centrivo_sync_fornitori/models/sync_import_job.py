# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.sync.import.job — import prodotti ASINCRONO in background (Flusso A).

Perché esiste: l'import sincrono girava dentro la request HTTP del worker Odoo e
veniva TRONCATO dal limite di tempo della richiesta (`limit_time_real`, 120s) su
file grandi (verificato: CARFER 3167 righe si fermava ~700). Qui il lavoro viene
ACCODATO (un record persistente con il file allegato + la mappatura del giro
serializzata) ed eseguito da un CRON nativo Odoo.

Meccanismo: elaborazione a CHUNK con cursore persistito. Ogni tick del cron
elabora UN blocco (righe o immagini), committa il cursore e si RI-ARMA finché il
job non è concluso. Conseguenze volute:
  - nessun singolo tick si avvicina al limite di tempo del cron worker;
  - RIPRESA automatica: se un tick viene interrotto, il successivo riparte dal
    cursore committato; il match per codice fornitore (idempotente) evita
    duplicati (i prodotti già creati vengono riconosciuti e aggiornati);
  - immagini (FASE 2) incluse nel job, resilienti (un URL fallito non blocca).

Disciplina: ORM (mai SQL), company_id dal canale, namespace centrivo.,
nessun segreto loggato.
"""
import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..connectors import catalog_parser as cp

_logger = logging.getLogger(__name__)

# Dimensione dei blocchi per singolo tick di cron. Tenuti piccoli per restare
# ben sotto qualunque limite di tempo del cron worker; la ripresa da cursore
# rende comunque innocua un'eventuale interruzione a metà blocco. In FASE 2 il
# blocco è in RIGHE del file (≈1 download per riga con immagine).
_ROW_CHUNK = 200
_IMAGE_CHUNK = 6

# Cadenza (in righe) delle righe di progresso nel Log operazioni (≈ come FASE 1).
_LOG_EVERY = 60

_XMLID_CRON = "centrivo_sync_fornitori.cron_run_import_jobs"


class CentrivoSyncImportJob(models.Model):
    _name = "centrivo.sync.import.job"
    _description = "Job di import prodotti (asincrono, background)"
    _order = "create_date desc"

    name = fields.Char(
        string="Riferimento", compute="_compute_name", store=True)
    channel_id = fields.Many2one(
        "centrivo.sync.channel", string="Fornitore", required=True,
        ondelete="cascade", index=True)
    company_id = fields.Many2one(
        related="channel_id.company_id", string="Azienda", store=True)

    state = fields.Selection(
        selection=[
            ("queued", "In coda"), ("running", "In corso"),
            ("done", "Completato"), ("error", "Con errori"),
            ("cancelled", "Annullato")],
        string="Stato", default="queued", required=True, index=True)
    phase = fields.Selection(
        selection=[
            ("rows", "Anagrafiche"), ("images", "Immagini"),
            ("done", "Concluso")],
        string="Fase", default="rows")

    # File + mappatura persistiti: il background non ha più il wizard transient.
    upload_file = fields.Binary(string="File", attachment=True)
    upload_filename = fields.Char(string="Nome file")
    spec_json = fields.Text(string="Mappatura colonne (JSON)")

    total_rows = fields.Integer(string="Righe totali")
    row_cursor = fields.Integer(string="Righe elaborate", default=0)
    # FASE 2: il cursore scorre le RIGHE del file (immagini derivate dal file).
    image_total = fields.Integer(string="Righe per immagini")
    image_cursor = fields.Integer(string="Righe immagini elaborate", default=0)

    created_count = fields.Integer(string="Creati")
    updated_count = fields.Integer(string="Aggiornati")
    skipped_count = fields.Integer(string="Saltati")
    error_count = fields.Integer(string="Errori")
    image_ok = fields.Integer(string="Immagini scaricate")
    image_failed = fields.Integer(string="Immagini fallite")
    image_skipped = fields.Integer(
        string="Immagini saltate",
        help="Righe senza URL immagine, prodotto non trovato o immagine già "
             "presente (image_1920 valorizzato).")

    message = fields.Char(string="Ultimo stato")
    started_at = fields.Datetime(string="Avvio")
    finished_at = fields.Datetime(string="Fine")

    @api.depends("channel_id", "upload_filename")
    def _compute_name(self):
        for job in self:
            base = job.channel_id.name or "Import"
            job.name = "%s — %s" % (base, job.upload_filename or "file")

    # ------------------------------------------------------------------
    # Azioni UI
    # ------------------------------------------------------------------
    def action_cancel(self):
        """Annulla un job in coda o in corso (il cron lo salta)."""
        for job in self:
            if job.state in ("queued", "running"):
                job.write({
                    "state": "cancelled",
                    "finished_at": fields.Datetime.now(),
                    "message": _("Annullato dall'operatore."),
                })

    def action_requeue(self):
        """Rimette in coda un job concluso/errato/annullato: la ripresa riparte
        dai cursori correnti (idempotente) e completa le righe/immagini mancanti."""
        for job in self:
            if job.state in ("done", "error", "cancelled"):
                job.write({"state": "queued", "message": _("Rimesso in coda.")})
        self._arm_cron()

    def action_run_images(self):
        """(Ri)avvia la FASE 2 immagini sui prodotti GIÀ creati: scansiona il file
        e scarica le immagini mancanti (dove `image_1920` è vuoto), in modo
        idempotente. È il modo per COMPLETARE le immagini di un import la cui
        anagrafica è già stata fatta (anche in un run precedente). Riparte dalla
        prima riga (cursore FASE 2 = 0); le immagini già presenti vengono saltate.
        """
        for job in self:
            specs = json.loads(job.spec_json or "[]")
            if not job.channel_id._image_header(specs):
                raise UserError(_(
                    "Questo job non ha una colonna 'URL immagine principale' "
                    "mappata e attiva: niente immagini da scaricare."))
            job.write({
                "state": "queued",
                "phase": "images",
                "image_cursor": 0,
                "image_total": 0,
                "image_ok": 0,
                "image_failed": 0,
                "image_skipped": 0,
                "message": _("FASE 2 immagini ri-accodata."),
            })
        self._arm_cron()

    # ------------------------------------------------------------------
    # Cron (esecuzione background)
    # ------------------------------------------------------------------
    @api.model
    def cron_run_import_jobs(self):
        """Cron: elabora UN chunk del job attivo, poi si ri-arma se resta lavoro.

        Un solo job per tick (prima i 'running' da riprendere, poi il più vecchio
        'queued') e un solo chunk: così ogni esecuzione resta breve. Se il job non
        è concluso, ri-triggera il cron (chunk successivo back-to-back, senza
        attendere l'intervallo periodico).
        """
        job = self.search([("state", "=", "running")], order="create_date", limit=1)
        if not job:
            job = self.search([("state", "=", "queued")], order="create_date", limit=1)
        if not job:
            return
        job._process_chunk()
        # Ri-arma finché resta lavoro (questo job non finito, o altri in coda):
        # i chunk/job scorrono back-to-back senza attendere l'intervallo periodico.
        if self.search_count([("state", "in", ("queued", "running"))]):
            self._arm_cron()

    @api.model
    def _arm_cron(self):
        """Triggera il cron il prima possibile (dopo il commit della request)."""
        cron = self.env.ref(_XMLID_CRON, raise_if_not_found=False)
        if cron:
            cron._trigger()

    def _process_chunk(self):
        """Esegue UN blocco della fase corrente, con commit del cursore.

        Cattura qualsiasi eccezione: il job va in 'error' (con rollback) ma il
        cron worker resta sano. Lo stato precedente già committato non si perde.
        """
        self.ensure_one()
        if self.state == "cancelled":
            return
        if self.state == "queued":
            self.write({
                "state": "running",
                "started_at": fields.Datetime.now(),
                "message": _("Avvio import in background..."),
            })
            self.env.cr.commit()
        try:
            if self.phase == "rows":
                self._process_rows_chunk()
            elif self.phase == "images":
                self._process_images_chunk()
            else:
                self._finish()
        except Exception as exc:  # noqa: BLE001 — un job non deve rompere il cron
            self.env.cr.rollback()
            self.write({
                "state": "error",
                "finished_at": fields.Datetime.now(),
                "message": _("Errore: %s") % exc,
            })
            self.env.cr.commit()
            self.channel_id._log(
                "sync_import", "error", "Job import #%s: %s" % (self.id, exc))
            _logger.exception("Job import #%s fallito", self.id)

    def _process_rows_chunk(self):
        """FASE 1: elabora il blocco di righe a partire da `row_cursor`."""
        self.ensure_one()
        content = base64.b64decode(self.upload_file or b"")
        if not content:
            raise ValueError(_("File del job mancante o illeggibile."))
        _headers, rows = cp.read_table(content, self.upload_filename)
        if not rows:
            raise ValueError(_("Il file non contiene righe dati."))
        if not self.total_rows:
            self.total_rows = len(rows)
        specs = json.loads(self.spec_json or "[]")
        start = self.row_cursor
        end = min(start + _ROW_CHUNK, len(rows))
        stats, _image_jobs = self.channel_id._import_rows(
            rows[start:end], specs, start_index=start)
        self.write({
            "created_count": self.created_count + stats["created"],
            "updated_count": self.updated_count + stats["updated"],
            "skipped_count": self.skipped_count + stats["skipped"],
            "error_count": self.error_count + stats["errors"],
            "row_cursor": end,
            "message": _("Fase 1 anagrafiche: %s/%s righe.") % (end, len(rows)),
        })
        self.env.cr.commit()
        if end >= len(rows):
            self.channel_id._log(
                "sync_import", "success",
                "Job #%s FASE 1 conclusa: creati %s, aggiornati %s, saltati %s, "
                "errori %s." % (self.id, self.created_count, self.updated_count,
                                self.skipped_count, self.error_count))
            # Transizione a FASE 2 se la colonna immagine è mappata/attiva. La
            # FASE 2 è DERIVATA DAL FILE (non da una lista accumulata): robusta e
            # ri-eseguibile sui prodotti già creati.
            if self.channel_id._image_header(specs):
                self.write({
                    "phase": "images",
                    "image_cursor": 0,
                    "image_total": len(rows),
                })
                self.channel_id._log(
                    "sync_import_image", "success",
                    "Job #%s avvio FASE 2 immagini su %s righe."
                    % (self.id, len(rows)))
            else:
                self._finish()
            self.env.cr.commit()

    def _process_images_chunk(self):
        """FASE 2: scarica il blocco di immagini (DERIVATO DAL FILE).

        Scorre le righe del file a partire da `image_cursor`, scaricando le
        immagini dei prodotti già esistenti dove `image_1920` è vuoto. Emette
        progresso nel Log operazioni (≈ ogni _LOG_EVERY righe) e un riepilogo a
        fine fase. Idempotente: le immagini già presenti vengono saltate.
        """
        self.ensure_one()
        content = base64.b64decode(self.upload_file or b"")
        if not content:
            raise ValueError(_("File del job mancante o illeggibile."))
        _headers, rows = cp.read_table(content, self.upload_filename)
        specs = json.loads(self.spec_json or "[]")
        if not self.image_total:
            self.image_total = len(rows)
        start = self.image_cursor
        end = min(start + _IMAGE_CHUNK, len(rows))
        ok, failed, skipped = self.channel_id._download_images_for_rows(
            rows[start:end], specs)
        self.write({
            "image_ok": self.image_ok + ok,
            "image_failed": self.image_failed + failed,
            "image_skipped": self.image_skipped + skipped,
            "image_cursor": end,
            "message": _(
                "Fase 2 immagini: righe %s/%s — scaricate %s, fallite %s, "
                "saltate %s.") % (end, len(rows), self.image_ok, self.image_failed,
                                  self.image_skipped),
        })
        self.env.cr.commit()
        # Progresso nel Log operazioni a cadenza ~_LOG_EVERY righe (e a fine fase).
        if end >= len(rows) or (end // _LOG_EVERY) != (start // _LOG_EVERY):
            self.channel_id._log(
                "sync_import_image", "success",
                "Job #%s FASE 2 progresso: righe %s/%s, scaricate %s, fallite %s, "
                "saltate %s." % (self.id, end, len(rows), self.image_ok,
                                 self.image_failed, self.image_skipped))
        if end >= len(rows):
            self.channel_id._log(
                "sync_import_image", "success",
                "Job #%s FASE 2 conclusa: scaricate %s, fallite %s, saltate %s."
                % (self.id, self.image_ok, self.image_failed, self.image_skipped))
            self._finish()
            self.env.cr.commit()

    def _finish(self):
        """Chiude il job: stato 'done' e timbro ultimo import sul canale."""
        self.ensure_one()
        self.write({
            "state": "done",
            "phase": "done",
            "finished_at": fields.Datetime.now(),
            "message": _(
                "Completato: %s creati, %s aggiornati, %s saltati, %s errori; "
                "immagini %s ok / %s ko / %s saltate.") % (
                self.created_count, self.updated_count, self.skipped_count,
                self.error_count, self.image_ok, self.image_failed,
                self.image_skipped),
        })
        self.channel_id.sudo().write(
            {"last_catalog_import": fields.Datetime.now()})
        self.channel_id._log(
            "sync_import", "success", "Job #%s %s" % (self.id, self.message))
