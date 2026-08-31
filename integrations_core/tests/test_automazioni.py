# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La schermata delle automazioni del pacchetto.

⚠️ **Il difetto che queste prove esistono per impedire, ed è già successo.**
`ir.cron` ha il campo `active`, e Odoo **nasconde da solo i record disattivati**
in qualunque elenco. Le automazioni di questo pacchetto **nascono tutte spente**:
la schermata mostrava quindi solo le 5 accese — cioè **esattamente il contrario
del suo scopo**, che è accendere quelle spente. Visto da Angelo su stage il
2026-08-31: «vedo solo 5 azioni».

⚠️ Ed è un difetto che si presenta bene: la schermata non dà errore, non è
vuota, mostra un elenco plausibile. Nessuno la guarderebbe due volte.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_core")
class TestAutomazioniCentrivo(TransactionCase):

    def _azione(self):
        return self.env["centrivo.channel"].action_centrivo_automazioni()

    def _nostri_cron(self):
        """Le automazioni del pacchetto, spente comprese."""
        dati = self.env["ir.model.data"].sudo().search([("model", "=", "ir.cron")])
        nostri = dati.filtered(
            lambda d: d.module == "integrations_core"
            or d.module.startswith(("marketplace_", "centrivo_")))
        return self.env["ir.cron"].sudo().with_context(
            active_test=False).browse(nostri.mapped("res_id")).exists()

    # ------------------------------------------------------------------
    def test_01_l_elenco_comprende_le_automazioni_SPENTE(self):
        """⚠️ È la prova che conta: una schermata per accendere le automazioni
        che non fa vedere quelle spente non serve a niente."""
        spente = self._nostri_cron().filtered(lambda c: not c.active)
        self.assertTrue(
            spente,
            "Il pacchetto deve avere automazioni spente, o questa prova non "
            "sta misurando niente.")
        azione = self._azione()
        visti = self.env["ir.cron"].with_context(
            **azione.get("context", {})).search(azione["domain"])
        mancanti = spente - visti
        self.assertFalse(
            mancanti,
            "Queste automazioni SPENTE non si vedono: %s"
            % mancanti.with_context(active_test=False).mapped("name"))

    def test_02_ci_sono_anche_quelle_accese(self):
        """Non basta rovesciare il filtro: servono tutte e due."""
        azione = self._azione()
        visti = self.env["ir.cron"].with_context(
            **azione.get("context", {})).search(azione["domain"])
        accese = self._nostri_cron().filtered("active")
        self.assertTrue(accese)
        self.assertFalse(accese - visti, "Mancano automazioni accese.")

    def test_03_NON_ci_finiscono_le_automazioni_di_Odoo(self):
        """⚠️ L'altra metà del motivo per cui questa schermata esiste: se
        mostrasse anche le automazioni di Odoo (fatture, magazzino, posta),
        sarebbe il menu tecnico con un nome diverso — e spegnerne una per
        sbaglio è un guaio che nessuno collegherebbe a questo pacchetto."""
        azione = self._azione()
        visti = self.env["ir.cron"].with_context(
            **azione.get("context", {})).search(azione["domain"])
        estranei = visti - self._nostri_cron()
        self.assertFalse(
            estranei,
            "Non sono nostre: %s"
            % estranei.with_context(active_test=False).mapped("name"))

    def test_04_le_due_che_girano_su_modelli_di_Odoo_ci_sono(self):
        """⚠️ Due automazioni del pacchetto girano su modelli di Odoo
        (`stock.picking` per la conferma automatica, `account.move` per le
        fatture ManoMano). Un elenco filtrato per MODELLO le perderebbe — e
        sono proprio le due che nessuno andrebbe a cercare."""
        azione = self._azione()
        visti = self.env["ir.cron"].with_context(
            **azione.get("context", {})).search(azione["domain"])
        modelli = set(visti.mapped("model_id.model"))
        for atteso in ("stock.picking", "account.move"):
            if atteso in set(self._nostri_cron().mapped("model_id.model")):
                self.assertIn(
                    atteso, modelli,
                    "L'automazione su %s è del pacchetto e deve comparire."
                    % atteso)
