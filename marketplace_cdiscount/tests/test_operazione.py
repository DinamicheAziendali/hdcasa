# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""La colonna «Su Cdiscount» (`operazione`) e il lettore del rapporto dentro
un Odoo vero — Consegna 1-bis del 2026-09-02.

⚠️ Perché queste prove esistono: la sonda in sola lettura sull'account vero
(`docs/cdiscount-misurato-2026-09-02.md`) ha mostrato che il rapporto di
Cdiscount porta, riga per riga, `operationType` — la risposta a «esisteva
già?» che arriva SOLO dopo aver mandato. Il connettore la scrive sulla
scheda; qui si prova che la colonna esiste davvero nel database, che accetta
esattamente i tre valori che il lettore produce, e che il lettore importato
dentro Odoo legge la forma misurata.
"""
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.marketplace_cdiscount.connectors.cdiscount_rapporto import (
    ATTESA, CREAZIONE, IDENTICA, IN_LAVORAZIONE, MODIFICA, PRONTO, RIFIUTATO,
    RIUSCITO, leggi_rapporto, riconcilia)
from odoo.addons.marketplace_cdiscount.models.cdiscount_scheda import (
    SCONOSCIUTO_SCHEDA)


@tagged("post_install", "-at_install", "centrivo_cdiscount")
class TestOperazione(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.canale = cls.env["centrivo.channel"].create({
            "name": "Cdiscount (prova operazione)",
            "connector_code": "cdiscount",
            "company_id": cls.env.company.id,
        })
        cls.Scheda = cls.env["cdiscount.scheda"]

    def _scheda(self, codice, **valori):
        valori.setdefault("channel_id", self.canale.id)
        valori.setdefault("codice", codice)
        return self.Scheda.create(valori)

    def test_01_la_colonna_esiste_e_nasce_vuota(self):
        scheda = self._scheda("HDC-OP-1")
        self.assertFalse(scheda.operazione)
        self.assertEqual(scheda.stato, SCONOSCIUTO_SCHEDA)

    def test_02_i_tre_valori_sono_quelli_del_lettore(self):
        """⚠️ La Selection e il lettore devono dire le STESSE stringhe: il
        connettore scrive sulla scheda il valore che `leggi_rapporto` rende,
        e un valore fuori dalla Selection farebbe fallire la `write` a meta'
        raccolta."""
        campo = self.Scheda._fields["operazione"]
        ammessi = [valore for valore, _etichetta in campo.selection]
        self.assertEqual(ammessi, [CREAZIONE, MODIFICA, IDENTICA])
        for valore in ammessi:
            scheda = self._scheda("HDC-OP-%s" % valore)
            scheda.write({"operazione": valore})
            self.assertEqual(scheda.operazione, valore)

    def test_03_il_lettore_legge_la_forma_misurata(self):
        """La forma vera del rapporto, letta dentro Odoo: `items` con uno
        stato per riga e `operationType`."""
        stato, esiti = leggi_rapporto({"itemsPerPage": 3, "items": [
            {"sellerProductReference": "A", "status": "Integrated",
             "operationType": "Creation"},
            {"sellerProductReference": "B", "status": "Refused",
             "operationType": "Creation",
             "errors": [{"code": "IncorrectValue", "field": "Brand",
                         "message": "marque inconnue", "type": "Exports"}]},
            {"sellerProductReference": "C", "status": "Validated",
             "operationType": "Identical"},
        ]})
        self.assertEqual(stato, PRONTO)
        self.assertEqual(esiti["A"], {"esito": RIUSCITO, "motivo": "",
                                      "operazione": CREAZIONE})
        self.assertEqual(esiti["B"]["esito"], RIFIUTATO)
        self.assertIn("Brand", esiti["B"]["motivo"])
        self.assertIn("marque inconnue", esiti["B"]["motivo"])
        self.assertEqual(esiti["C"]["esito"], ATTESA)
        self.assertEqual(esiti["C"]["operazione"], IDENTICA)
        conti = riconcilia(["A", "B", "C", "D"], esiti)
        self.assertEqual(conti, {"confermati": 1, "rifiutati": 1,
                                 "mancanti": ["C", "D"], "estranee": [],
                                 "attese": ["C"]})

    def test_04_un_rapporto_vuoto_e_in_lavorazione(self):
        """⚠️ MISURATO: per un numero valido Cdiscount risponde 200 e
        `items: []` finché ci lavora. Non è un rapporto muto e non è un
        guasto."""
        self.assertEqual(leggi_rapporto({"itemsPerPage": 0, "items": []}),
                         (IN_LAVORAZIONE, {}))

    def test_05_la_forma_vecchia_non_si_sa_leggere(self):
        """La forma della prima documentazione (`status`/`results`) non deve
        produrre nessun verdetto: né vuoto, né in lavorazione."""
        stato, esiti = leggi_rapporto({"status": "Completed", "results": [
            {"sellerProductRef": "A", "status": "Success"}]})
        self.assertNotEqual(stato, PRONTO)
        self.assertNotEqual(stato, IN_LAVORAZIONE)
        self.assertEqual(esiti, {})
