# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La scheda del canale mostra solo ciò che quel marketplace usa davvero.

⚠️ Il difetto, visto da Angelo il 2026-08-31 sul canale Kaufland in produzione:
la scheda mostrava la **chiave API di BricoBravo**, la **mappatura delle
colonne** di un feed CSV che Kaufland non fa, un bottone «Genera feed» che non
fa niente, un riquadro intitolato **«URL DEI FEED — DA INCOLLARE SU
BRICOBRAVO»**, e — la peggiore — **«Ambiente: Sandbox (test)»** su un canale il
cui indirizzo era `sellerapi.kaufland.com`, cioè **il Kaufland vero**.

⚠️ **Perché non con un `invisible` per nome di marketplace.** ManoMano lo
faceva così, dalle sue viste. Funzionava, ma due moduli che riscrivono lo
STESSO attributo sullo STESSO nodo **si sovrascrivono a vicenda**: vince chi si
carica per ultimo, e l'altro smette di nascondere **senza che nessun errore lo
dica**. Kaufland stava per aggiungere i suoi sugli stessi identici nodi.

Ora ogni connettore **dichiara** cosa usa e il tronco chiede. Il default è
**True**: un connettore che si dimentica tiene la schermata di sempre — si
perde un po' di pulizia, non un campo che serviva.
"""
from odoo.tests.common import TransactionCase, tagged

INTERRUTTORI = ("usa_api_key", "usa_ambienti", "usa_indirizzo_base",
                "usa_feed_csv", "usa_immagini_feed", "usa_mappa_catalogo")


@tagged("post_install", "-at_install", "centrivo_core")
class TestCosaUsaIlCanale(TransactionCase):

    def _canale(self, codice):
        return self.env["centrivo.channel"].create({
            "name": "Prova %s" % codice, "connector_code": codice,
            "company_id": self.env.company.id})

    # ------------------------------------------------------------------
    def test_01_BricoBravo_non_perde_niente(self):
        """⚠️ Viene per primo perché è il canale che GIRA IN PRODUZIONE: la
        scheda attorno a cui tutto questo è cresciuto è la sua, e questa
        pulizia non deve togliergli niente."""
        canale = self._canale("bricobravo")
        for nome in INTERRUTTORI:
            self.assertTrue(canale[nome],
                            "BricoBravo deve continuare a vedere %s." % nome)

    def test_02_ManoMano_vede_quello_che_vedeva_prima(self):
        """Prima lo nascondeva da sé con cinque `invisible`. Il risultato a
        video dev'essere identico, o la sostituzione è una regressione."""
        canale = self._canale("manomano")
        self.assertTrue(canale.usa_api_key, "ManoMano la chiave API la usa.")
        self.assertTrue(canale.usa_ambienti, "E ha sandbox e produzione.")
        # ⚠️ Questa è la sottile: la risoluzione delle immagini le SERVE,
        # perché i feed prodotto li fa. Stava in un gruppo a sé apposta.
        self.assertTrue(canale.usa_immagini_feed)
        self.assertFalse(canale.usa_indirizzo_base, "L'URL lo sceglie l'ambiente.")
        self.assertFalse(canale.usa_feed_csv)
        self.assertFalse(canale.usa_mappa_catalogo)

    def test_03_Kaufland_non_mostra_piu_roba_di_BricoBravo(self):
        canale = self._canale("kaufland")
        self.assertFalse(canale.usa_api_key)
        self.assertFalse(canale.usa_feed_csv)
        self.assertFalse(canale.usa_immagini_feed)
        self.assertFalse(canale.usa_mappa_catalogo)
        self.assertTrue(canale.usa_indirizzo_base,
                        "L'indirizzo Kaufland si vede e si usa.")

    def test_04_Kaufland_NON_ha_un_ambiente_di_prova(self):
        """⚠️ La più importante delle quattro. Il campo diceva «Sandbox (test)»
        su un canale che parlava col Kaufland VERO: una schermata che dice
        «sei in prova» mentre scrivi su un marketplace vero è peggio di una
        schermata muta."""
        self.assertFalse(self._canale("kaufland").usa_ambienti)

    def test_05_Temu_e_Cdiscount_come_Kaufland(self):
        for codice in ("temu", "cdiscount"):
            canale = self._canale(codice)
            self.assertFalse(canale.usa_api_key, codice)
            self.assertFalse(canale.usa_ambienti, codice)
            self.assertFalse(canale.usa_feed_csv, codice)
            self.assertFalse(canale.usa_mappa_catalogo, codice)

    def test_06_un_connettore_sconosciuto_mostra_TUTTO(self):
        """⚠️ Il verso sicuro, e va difeso: nascondere per difetto farebbe
        sparire in silenzio la configurazione di un canale che gira."""
        canale = self._canale("bricobravo")
        canale.invalidate_recordset()
        # Si forza un codice che nessun connettore registra.
        canale.sudo().write({"connector_code": "bricobravo"})
        from odoo.addons.integrations_core.connectors.base import (
            MarketplaceConnector)
        for nome in INTERRUTTORI:
            self.assertTrue(
                MarketplaceConnector.usa_per("marketplace-inesistente", nome),
                "Un connettore sconosciuto deve mostrare %s." % nome)

    def test_07_niente_nomi_di_marketplace_nella_vista_del_tronco(self):
        """⚠️ È la regola che questa pulizia esiste per rispettare: il tronco
        chiede, i moduli rispondono. Un `connector_code == 'kaufland'` qui
        dentro sarebbe il ritorno del problema."""
        arch = self.env.ref(
            "integrations_core.view_integration_channel_form").arch
        for nome in ("kaufland", "manomano", "temu", "cdiscount", "bricobravo"):
            self.assertNotIn(
                "'%s'" % nome, arch,
                "La vista del tronco non deve nominare %s." % nome)
