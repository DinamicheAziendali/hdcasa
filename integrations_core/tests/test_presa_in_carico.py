# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""«Acquisito su marketplace» diceva una cosa falsa sugli ordini Kaufland.

⚠️ Chiesto da Angelo il 2026-08-31, mezz'ora dopo i primi ordini Kaufland veri:
*«i 3 Kaufland non hanno la spunta acquisito su marketplace, come mai?»*.

La colonna significa: **spunta assente + stato Importato = la presa in carico è
FALLITA e verrà ritentata** — e la lista la dipingeva pure **arancione** per
dirlo. Su BricoBravo e ManoMano è vero: una presa in carico ce l'hanno.

**Su Kaufland quella chiamata NON ESISTE.** L'ordine nasce `open`, dopo quindici
minuti passa a «da spedire», e il gesto dopo è direttamente la spedizione. La
casella sarebbe rimasta vuota e arancione **per sempre**, su ogni ordine.

⚠️ E non si poteva nascondere la colonna: «Ordini importati» è una lista
**mista**, e una colonna è la stessa per tutte le righe. Per questo non si
nasconde il booleano — si mostra un campo che sa dire anche «Non prevista».
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_core")
class TestPresaInCarico(TransactionCase):

    def _mappa(self, codice, acquisito=False):
        canale = self.env["centrivo.channel"].create({
            "name": "Prova %s" % codice, "connector_code": codice,
            "company_id": self.env.company.id})
        return self.env["centrivo.order.map"].create({
            "channel_id": canale.id, "external_id": "X-%s" % codice,
            "state": "imported", "acquired_done": acquisito,
            "company_id": self.env.company.id})

    # ------------------------------------------------------------------
    def test_01_su_Kaufland_la_presa_in_carico_e_NON_PREVISTA(self):
        """⚠️ È la domanda di Angelo, e la risposta non dev'essere «vuoto»."""
        self.assertEqual(self._mappa("kaufland").presa_in_carico,
                         "non_prevista")

    def test_02_e_resta_non_prevista_qualunque_cosa_dica_il_booleano(self):
        """Il booleano sotto non c'entra: su Kaufland non lo scrive nessuno,
        e se qualcuno lo scrivesse non cambierebbe la verità."""
        self.assertEqual(self._mappa("kaufland", acquisito=True).presa_in_carico,
                         "non_prevista")

    def test_03_su_BricoBravo_DA_FARE_vuol_dire_ancora_da_fare(self):
        """⚠️ Difende il significato che non deve andare perso: su chi la
        presa in carico ce l'ha, «da fare» è una cosa che qualcuno deve
        guardare."""
        self.assertEqual(self._mappa("bricobravo").presa_in_carico, "da_fare")

    def test_04_su_BricoBravo_fatta_quando_e_fatta(self):
        self.assertEqual(
            self._mappa("bricobravo", acquisito=True).presa_in_carico, "fatta")

    def test_05_ManoMano_ce_l_ha(self):
        """Con un altro nome — «accetta ordini» — ma è lo stesso passo."""
        self.assertEqual(self._mappa("manomano").presa_in_carico, "da_fare")

    def test_06_Temu_e_Cdiscount_come_Kaufland(self):
        for codice in ("temu", "cdiscount"):
            self.assertEqual(self._mappa(codice).presa_in_carico,
                             "non_prevista", codice)

    def test_07_la_colonna_della_lista_NON_e_piu_il_booleano_nudo(self):
        """⚠️ Difende il rimedio, non solo il calcolo: se la lista tornasse a
        mostrare `acquired_done`, il campo nuovo esisterebbe e la schermata
        continuerebbe a mentire."""
        arch = self.env.ref(
            "integrations_core.view_integration_order_map_list").arch
        self.assertIn('name="presa_in_carico"', arch)
        self.assertNotIn('name="acquired_done"', arch)
