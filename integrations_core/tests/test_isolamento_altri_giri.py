# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Gli altri due giri che promettono l'isolamento per canale.

Il primo — il feed prezzi/giacenze — sta in `test_isolamento_canali.py`, col
racconto di perche' un guasto VERO del database e' l'unico modo di provarlo.
Qui gli altri due, che fanno la stessa promessa in due punti diversi:

  - `_run_generate_catalog_feed`  (feed catalogo)
  - `cron_pull_all_channels`      (scarico ordini)

⚠️ E non sono una copia del primo: lo scarico ordini **si cerca i canali da
solo** e non passa dal connettore, ma da `action_pull_orders`. Provarlo come il
gemello vorrebbe dire provare una strada che non esiste.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


class BaseTreCanali(TransactionCase):
    """Tre canali col guasto IN MEZZO.

    ⚠️ L'ordine non e' cosmetico: col guasto in fondo la prova passerebbe anche
    su un codice senza isolamento, perche' non ci sarebbe nessun canale «dopo»
    da servire.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Canale = cls.env["centrivo.channel"]
        codice = Canale._get_connector_selection()[0][0]
        cls.primo, cls.guasto, cls.terzo = [
            Canale.create({"name": nome, "connector_code": codice,
                           "company_id": cls.env.company.id})
            for nome in ("Primo (sano)", "Secondo (rompe il database)",
                         "Terzo (sano)")]
        cls.tutti = cls.primo | cls.guasto | cls.terzo

    def _rompe(self, canale):
        return canale.id == self.guasto.id

    @staticmethod
    def _spacca(canale):
        """Un guasto che viene da PostgreSQL, non un'eccezione Python.

        ⚠️ Un `RuntimeError` lascerebbe la transazione sana, e il giro
        proseguirebbe **anche senza savepoint**: la prova passerebbe su un
        codice rotto.
        """
        canale.env.cr.execute("SELECT 1 / 0")

    def _letto_dal_database(self, canale, colonna):
        """Il valore in SQL, non dalla cache dell'ORM.

        ⚠️ La cache restituirebbe cio' che e' stato scritto anche se non ha mai
        raggiunto il database — ed e' cosi' che due prove del gemello
        passavano pur col difetto rimesso.
        """
        self.env.cr.execute(
            "SELECT %s FROM centrivo_channel WHERE id = %%s" % colonna,
            (canale.id,))
        riga = self.env.cr.fetchone()
        return riga[0] if riga else None

    def _registro(self, canale, operazione, esito="error"):
        return self.env["centrivo.job.log"].sudo().search([
            ("channel_id", "=", canale.id),
            ("operation", "=", operazione),
            ("result", "=", esito),
        ])


# ===========================================================================
# GIRO 2 — il feed catalogo
# ===========================================================================
class ConnettoreFintoCatalogo(object):

    def __init__(self, canale, rompe, spacca):
        self.canale = canale
        self.rompe = rompe
        self._spacca = spacca

    def generate_catalog_feed(self):
        if self.rompe:
            self._spacca(self.canale)
            return True
        self.canale.write({
            "catalog_feed_content": "catalogo di %s" % self.canale.name,
            "catalog_feed_generated_at": fields.Datetime.now(),
        })
        # Il flush porta il lavoro DAVVERO in SQL, prima che il canale
        # seguente abortisca la transazione.
        self.canale.env.flush_all()
        return True


@tagged("post_install", "-at_install", "centrivo_core")
class TestIsolamentoFeedCatalogo(BaseTreCanali):

    def _gira(self):
        def finto(canale):
            return ConnettoreFintoCatalogo(canale, self._rompe(canale),
                                           self._spacca)

        with patch.object(type(self.tutti), "_get_connector", finto):
            self.tutti._run_generate_catalog_feed()

    def test_01_il_giro_arriva_in_fondo(self):
        self._gira()

    def test_02_il_canale_DOPO_viene_servito(self):
        self._gira()
        self.assertTrue(
            self._letto_dal_database(self.terzo, "catalog_feed_content"),
            "Il canale dopo quello guasto dev'essere stato servito, e il suo "
            "catalogo dev'essere NEL DATABASE.")

    def test_03_il_lavoro_del_canale_PRIMA_sopravvive(self):
        self._gira()
        scritto = self._letto_dal_database(self.primo, "catalog_feed_content")
        self.assertTrue(scritto, "Il catalogo del primo canale dev'essere "
                                 "ancora nel database dopo il guasto.")
        self.assertIn("Primo", scritto)

    def test_04_la_transazione_resta_utilizzabile(self):
        self._gira()
        self.env["centrivo.channel"].create({
            "name": "Nato dopo il guasto (catalogo)",
            "connector_code": self.primo.connector_code,
            "company_id": self.env.company.id,
        })

    def test_05_l_errore_e_scritto_nel_registro(self):
        self._gira()
        righe = self._registro(self.guasto, "export_catalog_feed")
        self.assertEqual(len(righe), 1)

    def test_06_i_canali_sani_non_lasciano_errori(self):
        self._gira()
        self.assertFalse(self._registro(self.primo, "export_catalog_feed"))
        self.assertFalse(self._registro(self.terzo, "export_catalog_feed"))


# ===========================================================================
# GIRO 3 — lo scarico ordini
# ===========================================================================
@tagged("post_install", "-at_install", "centrivo_core")
class TestIsolamentoScaricoOrdini(BaseTreCanali):
    """⚠️ Questo giro ha una forma DIVERSA, e va provato per quella che ha.

    `cron_pull_all_channels` **si cerca i canali da solo** (tutti gli attivi) e
    non passa dal connettore: chiama `action_pull_orders` sul canale. Due
    conseguenze:

    1. si sostituisce `action_pull_orders`, non `_get_connector`;
    2. il giro tocca **tutti** i canali attivi del database, quelli veri
       compresi. La sostituzione vale per tutti — che oltre a rendere la prova
       possibile **impedisce di uscire in rete verso i marketplace veri**: su
       questo stage il canale Kaufland punta a `sellerapi.kaufland.com`.
    """

    def _gira(self):
        prova = self

        def finto(self_canale):
            for canale in self_canale:
                if prova._rompe(canale):
                    prova._spacca(canale)
                    continue
                # Il «lavoro» di uno scarico riuscito: la riga di registro che
                # farebbe davvero. Serve qualcosa di durevole da ritrovare.
                canale.env["centrivo.job.log"].sudo().create({
                    "channel_id": canale.id,
                    "operation": "pull_orders",
                    "result": "success",
                    "message": "scarico finto riuscito",
                    "company_id": canale.company_id.id,
                })
                canale.env.flush_all()
            return True

        with patch.object(type(self.tutti), "action_pull_orders", finto):
            self.env["centrivo.channel"].cron_pull_all_channels()

    def test_01_il_giro_arriva_in_fondo(self):
        self._gira()

    def test_02_il_canale_DOPO_quello_guasto_viene_servito(self):
        """⚠️ Il controllo che conta: prima il terzo canale quel giro non
        esisteva, e nessuno lo diceva."""
        self._gira()
        self.assertTrue(
            self._registro(self.terzo, "pull_orders", esito="success"),
            "Il canale dopo quello guasto dev'essere stato scaricato.")

    def test_03_il_lavoro_del_canale_PRIMA_sopravvive(self):
        self._gira()
        self.assertTrue(
            self._registro(self.primo, "pull_orders", esito="success"),
            "Lo scarico del primo canale dev'essere ancora nel registro dopo "
            "il guasto del secondo.")

    def test_04_la_transazione_resta_utilizzabile(self):
        self._gira()
        self.env["centrivo.channel"].create({
            "name": "Nato dopo il guasto (ordini)",
            "connector_code": self.primo.connector_code,
            "company_id": self.env.company.id,
        })

    def test_05_l_errore_e_scritto_nel_registro(self):
        self._gira()
        righe = self._registro(self.guasto, "pull_orders")
        self.assertEqual(
            len(righe), 1,
            "Il canale guasto deve lasciare una riga d'errore: senza, uno "
            "scarico che non e' avvenuto e' indistinguibile da uno vuoto.")

    def test_06_il_canale_guasto_non_lascia_un_successo(self):
        """Il guasto non deve anche dichiararsi riuscito.

        Sembra ovvio, e non lo e': la riga di successo la scrive il finto
        PRIMA di rompere in altri disegni possibili. Se comparisse, lo scarico
        risulterebbe fatto e nessuno tornerebbe a guardarlo.
        """
        self._gira()
        self.assertFalse(
            self._registro(self.guasto, "pull_orders", esito="success"))
