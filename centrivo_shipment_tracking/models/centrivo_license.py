# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""centrivo.license — client di registrazione verso il Centrivo License Server.

FASE 1 — SOLO registrazione best-effort. Nessun gating, nessuna verifica licenza,
nessun blocco di funzionalità: il modulo funziona identico con o senza risposta del
server. Quando l'istanza installa (o aggiorna) la suite tracking, inviamo un "ping
di registrazione" così sappiamo su quali database è installata.

Caratteristiche chiave:
  - URL del server da ir.config_parameter 'centrivo.license.server_url'
    (default https://license.centrivo.app/register), modificabile da
    Impostazioni → Tecnico → Parametri di sistema.
  - Contatto opzionale da ir.config_parameter 'centrivo.license.contact'.
  - Timeout corto (5s) + try/except totale: QUALUNQUE errore (timeout, DNS, server
    giù, risposta non valida) è silenzioso → log a debug, MAI propagato, MAI bloccante.
  - Idempotenza via ir.config_parameter 'centrivo.license.registered': un ping alla
    prima installazione e uno a ogni cambio-versione del modulo, non a ogni restart.

Trasparenza/conformità: il modulo contatta il license server di Centrivo
all'installazione/aggiornamento inviando i DATI MINIMI: dbuuid, product, versione del
modulo, URL dell'istanza e un contatto OPZIONALE. Nessun altro dato lascia l'istanza.
"""
import logging

import requests

from odoo import api, models

_logger = logging.getLogger(__name__)

# Modulo di cui leggiamo la versione e identità del prodotto nel payload.
MODULE_NAME = "centrivo_shipment_tracking"
PRODUCT = "tracking"

# Parametri di sistema (ir.config_parameter).
PARAM_SERVER_URL = "centrivo.license.server_url"
PARAM_CONTACT = "centrivo.license.contact"
PARAM_REGISTERED = "centrivo.license.registered"

# URL di default se il parametro non è impostato.
DEFAULT_SERVER_URL = "https://license.centrivo.app/register"

# Timeout corto: la registrazione è best-effort e non deve rallentare l'install.
REQUEST_TIMEOUT = 5


class CentrivoLicense(models.AbstractModel):
    _name = "centrivo.license"
    _description = "Client di registrazione license server Centrivo (best-effort)"

    # ------------------------------------------------------------------
    # Raccolta dati
    # ------------------------------------------------------------------
    @api.model
    def _get_module_version(self):
        """Versione installata del modulo tracking (dal manifest, via ir.module)."""
        module = self.env["ir.module.module"].sudo().search(
            [("name", "=", MODULE_NAME)], limit=1)
        # installed_version riflette la versione del manifest applicata al DB.
        return module.installed_version or module.latest_version or ""

    @api.model
    def _get_server_url(self):
        """URL del license server (parametro di sistema, con default)."""
        icp = self.env["ir.config_parameter"].sudo()
        return icp.get_param(PARAM_SERVER_URL, DEFAULT_SERVER_URL) or DEFAULT_SERVER_URL

    @api.model
    def _build_payload(self):
        """Payload minimo di registrazione (vedi nota di trasparenza in testa)."""
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "dbuuid": icp.get_param("database.uuid") or "",
            "product": PRODUCT,
            "module_version": self._get_module_version(),
            "instance_url": icp.get_param("web.base.url") or "",
            "contact": icp.get_param(PARAM_CONTACT) or "",
        }

    # ------------------------------------------------------------------
    # Invio (best-effort)
    # ------------------------------------------------------------------
    @api.model
    def _send_registration(self):
        """POST JSON best-effort al license server.

        Ritorna True se la chiamata è stata effettuata senza eccezioni, False se è
        fallita. In OGNI caso non solleva: qualunque errore è silenzioso e loggato a
        debug. La risposta NON viene usata per decidere nulla (fase 1: no gating).
        """
        url = self._get_server_url()
        payload = self._build_payload()
        try:
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            _logger.debug(
                "Centrivo license: registrazione inviata a %s (HTTP %s) "
                "payload=%s risposta=%s",
                url, resp.status_code, payload, (resp.text or "")[:500])
            return True
        except Exception as exc:  # noqa: BLE001 - best-effort, nessun errore propaga
            _logger.debug(
                "Centrivo license: registrazione verso %s non riuscita (ignorata, "
                "best-effort): %s", url, exc)
            return False

    # ------------------------------------------------------------------
    # Idempotenza + trigger
    # ------------------------------------------------------------------
    @api.model
    def _register_if_needed(self):
        """Invia il ping SOLO se prima installazione o cambio-versione del modulo.

        Confronta la versione salvata in 'centrivo.license.registered' con quella
        corrente: se assente o diversa invia e poi salva la versione corrente; se
        uguale non reinvia. Chiamata dal post_init_hook. Mai bloccante.
        """
        icp = self.env["ir.config_parameter"].sudo()
        current = self._get_module_version()
        registered = icp.get_param(PARAM_REGISTERED)
        if registered and current and registered == current:
            _logger.debug(
                "Centrivo license: versione %s già registrata, ping saltato.", current)
            return False
        self._send_registration()
        # Salviamo comunque la versione corrente (best-effort): evita un ping a ogni
        # restart anche se il server era irraggiungibile. Il ri-invio manuale resta
        # disponibile dal bottone "Registra ora" nelle impostazioni tracking.
        if current:
            icp.set_param(PARAM_REGISTERED, current)
        return True

    @api.model
    def action_register_now(self):
        """Re-invio manuale on-demand (bottone "Registra ora"). Ignora l'idempotenza."""
        sent = self._send_registration()
        current = self._get_module_version()
        if current:
            self.env["ir.config_parameter"].sudo().set_param(PARAM_REGISTERED, current)
        return sent
