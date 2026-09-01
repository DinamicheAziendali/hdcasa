# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il listino è PER MERCATO, non per canale.

⚠️ Il difetto che queste prove chiudono, visto da Angelo il 2026-08-31
guardando la configurazione in produzione: *«come faccio a impostare un listino
pieno unico se vendo in varie nazioni?»*.

Non si può, ed è lo stesso ragionamento della posizione fiscale: **il prezzo è
per Paese come l'IVA**. Un box doccia non costa lo stesso in Germania e in
Italia — concorrenza diversa, commissione diversa, spedizione diversa. Un campo
solo sul canale è già sbagliato al secondo mercato, e sbaglia **in silenzio**:
le offerte partono, nessun errore, e il prezzo è quello di un altro Paese.

La regola: **il listino del mercato vince; se il mercato non ne ha uno, si usa
quello del canale.** Così chi ha un mercato solo non deve compilare niente di
nuovo, e chi ne ha due può differenziare.
"""
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestListiniPerMercato(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        azienda = cls.env.company
        cls.prodotto = cls.env["product.product"].create({
            "name": "Box doccia (prova listini)",
            "default_code": "TEST-KFL-LIST-1",
            "barcode": "9990000000318", "list_price": 100.0})
        cls.listino_canale = cls.env["product.pricelist"].create({
            "name": "Listino del canale (prova)",
            "currency_id": azienda.currency_id.id,
            "item_ids": [(0, 0, {"applied_on": "3_global",
                                 "compute_price": "fixed",
                                 "fixed_price": 100.0})]})
        cls.listino_de = cls.env["product.pricelist"].create({
            "name": "Listino Germania (prova)",
            "currency_id": azienda.currency_id.id,
            "item_ids": [(0, 0, {"applied_on": "3_global",
                                 "compute_price": "fixed",
                                 "fixed_price": 230.0})]})
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Kaufland (prova listini)",
            "connector_code": "kaufland",
            "company_id": azienda.id,
            "kaufland_client_key": "chiave-finta",
            "kaufland_secret_key": "segreto-finto",
            "pricelist_selling_id": cls.listino_canale.id,
        })
        cls.mercato_de = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id, "storefront": "de",
            "shipping_group_id": "195841"})
        cls.mercato_it = cls.env["centrivo.kaufland.market"].create({
            "channel_id": cls.canale.id, "storefront": "it",
            "shipping_group_id": "195842"})

    def _listino_di(self, mercato):
        """Il listino che il connettore userebbe per QUEL mercato."""
        connettore = self.canale._get_connector()
        connettore._mercato_riga = mercato
        try:
            return connettore._listino_del_mercato()
        finally:
            connettore._mercato_riga = None

    # ------------------------------------------------------------------
    def test_01_senza_listino_sul_mercato_si_usa_quello_del_canale(self):
        """⚠️ Il ripiego, e viene per primo: chi ha un mercato solo non deve
        compilare niente di nuovo perché abbiamo aggiunto un campo."""
        self.assertEqual(self._listino_di(self.mercato_de), self.listino_canale)

    def test_02_il_listino_del_mercato_VINCE(self):
        self.mercato_de.pricelist_id = self.listino_de
        self.assertEqual(self._listino_di(self.mercato_de), self.listino_de)

    def test_03_due_mercati_due_listini_diversi(self):
        """È il caso di Angelo: Germania e Italia con prezzi diversi."""
        self.mercato_de.pricelist_id = self.listino_de
        self.assertEqual(self._listino_di(self.mercato_de), self.listino_de)
        self.assertEqual(self._listino_di(self.mercato_it), self.listino_canale,
                         "L'Italia deve restare su quello del canale.")

    def test_04_i_due_listini_danno_due_PREZZI_diversi(self):
        """Non basta che i record siano diversi: deve cambiare il prezzo."""
        self.mercato_de.pricelist_id = self.listino_de
        connettore = self.canale._get_connector()
        prezzo_de = connettore._pricelist_price(
            self._listino_di(self.mercato_de), self.prodotto)
        prezzo_it = connettore._pricelist_price(
            self._listino_di(self.mercato_it), self.prodotto)
        self.assertAlmostEqual(prezzo_de, 230.0, places=2)
        self.assertAlmostEqual(prezzo_it, 100.0, places=2)

    def test_05_senza_nessuno_dei_due_il_messaggio_nomina_IL_MERCATO(self):
        """⚠️ «Manca il listino sul canale» manderebbe a cercare nel posto
        sbagliato ora che il listino può stare sul mercato."""
        self.canale.pricelist_selling_id = False
        with self.assertRaises(UserError) as guasto:
            self._listino_di(self.mercato_de)
        testo = str(guasto.exception)
        self.assertIn("de", testo.lower(),
                      "Il messaggio deve dire QUALE mercato: %s" % testo)

    def test_06_il_campo_sul_mercato_e_un_listino_vero(self):
        """Difende il tipo: un Char col nome del listino sarebbe inutilizzabile."""
        campo = self.env["centrivo.kaufland.market"]._fields.get("pricelist_id")
        self.assertIsNotNone(campo, "Il campo deve esistere sul mercato.")
        self.assertEqual(campo.type, "many2one")
        self.assertEqual(campo.comodel_name, "product.pricelist")
