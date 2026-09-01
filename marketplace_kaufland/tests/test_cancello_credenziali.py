# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Cambiare le credenziali richiude il cancello — su OGNI mercato.

⚠️ **Il difetto che queste prove chiudono era vivo in produzione**, e l'ha
trovato Angelo il 2026-08-31 facendo la cosa più normale del mondo: incollare
chiave e segreto sul canale Kaufland. Errore in faccia:

    'centrivo.channel' object has no attribute 'kaufland_riagganciato'

**Cos'era successo.** Il 2026-08-29 il cancello del riaggancio è passato dal
canale alle righe dei mercati — giusto, perché è un fatto su UN mercato. Ma il
`write()` del canale, quello che richiude il cancello quando cambiano le
credenziali, **continuava a leggere i campi vecchi**, che sul canale non
esistono più. Restava lì, innocuo, finché nessuno toccava le credenziali.

⚠️ **E perché nessun banco l'aveva visto.** Tutte le prove **creano** il canale
con le credenziali dentro; **nessuna le riscrive dopo**. `create()` non passa
da quell'override. Un `write()` senza una prova che scriva non è coperto —
sembra coperto, che è peggio.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_kaufland")
class TestCancelloECredenziali(TransactionCase):

    def setUp(self):
        super().setUp()
        self.canale = self.env["centrivo.channel"].create({
            "name": "Kaufland (prova credenziali)",
            "connector_code": "kaufland",
            "company_id": self.env.company.id,
            "kaufland_client_key": "chiave-vecchia",
            "kaufland_secret_key": "segreto-vecchio",
        })
        self.de = self.env["centrivo.kaufland.market"].create({
            "channel_id": self.canale.id, "storefront": "de",
            "shipping_group_id": "195841"})
        self.it = self.env["centrivo.kaufland.market"].create({
            "channel_id": self.canale.id, "storefront": "it",
            "shipping_group_id": "195842"})
        self._apri_i_cancelli()

    def _apri_i_cancelli(self):
        (self.de | self.it).sudo().write({
            "riagganciato": True, "offerte_attese": 166})

    def _cancelli(self):
        return [m.sudo().riagganciato for m in (self.de, self.it)]

    # ------------------------------------------------------------------
    def test_01_scrivere_il_segreto_NON_esplode(self):
        """⚠️ È letteralmente il gesto di Angelo, ed è quello che si rompeva."""
        self.canale.sudo().write({"kaufland_secret_key": "segreto-nuovo"})
        self.assertEqual(self.canale.sudo().kaufland_secret_key,
                         "segreto-nuovo")

    def test_02_scrivere_la_chiave_NON_esplode(self):
        self.canale.sudo().write({"kaufland_client_key": "chiave-nuova"})
        self.assertEqual(self.canale.sudo().kaufland_client_key, "chiave-nuova")

    def test_03_il_segreto_nuovo_RICHIUDE_tutti_i_cancelli(self):
        """⚠️ Il cancello dice «so quali offerte esistono là, lette con QUELLE
        credenziali». Cambiate le credenziali la frase resta scritta e non
        parla più di niente — su ogni mercato del canale, non su uno."""
        self.canale.sudo().write({"kaufland_secret_key": "segreto-nuovo"})
        self.assertEqual(self._cancelli(), [False, False])

    def test_04_anche_l_indirizzo_richiude(self):
        self.canale.sudo().write({"base_url": "https://esempio.invalido/v2"})
        self.assertEqual(self._cancelli(), [False, False])

    def test_05_riscrivere_lo_STESSO_segreto_non_richiude_niente(self):
        """Un salvataggio che non cambia niente non deve costare un riaggancio
        a nessuno: sono 332 offerte da rileggere."""
        self.canale.sudo().write({"kaufland_secret_key": "segreto-vecchio"})
        self.assertEqual(self._cancelli(), [True, True])

    def test_06_cambiare_il_NOME_non_richiude_niente(self):
        self.canale.sudo().write({"name": "Kaufland (rinominato)"})
        self.assertEqual(self._cancelli(), [True, True])

    def test_07_un_canale_di_un_altro_marketplace_non_viene_toccato(self):
        """L'override sta sul modello condiviso: deve lasciar stare gli altri."""
        altro = self.env["centrivo.channel"].create({
            "name": "BricoBravo (prova)", "connector_code": "bricobravo",
            "company_id": self.env.company.id})
        altro.write({"base_url": "https://esempio.invalido/api"})
        self.assertEqual(altro.base_url, "https://esempio.invalido/api")

    # ------------------------------------------------------------------
    # Il gemello: il MERCATO che cambia identità
    # ------------------------------------------------------------------
    def test_08_cambiare_il_mercato_di_una_riga_richiude_QUEL_cancello(self):
        """⚠️ Lo stesso ragionamento, un piano più sotto: «riagganciato» su una
        riga vuol dire «di QUESTO mercato». Spostare la riga da `it` a `fr`
        lascerebbe il cancello alzato su un quadro di un altro Paese, e
        «Crea le offerte mancanti» metterebbe in vendita in Francia offerte
        mai riconosciute là."""
        self.it.sudo().write({"storefront": "fr"})
        self.assertFalse(self.it.sudo().riagganciato,
                         "Il cancello di quella riga deve richiudersi.")
        self.assertTrue(self.de.sudo().riagganciato,
                        "E quello dell'altro mercato NON si tocca.")

    def test_09_e_azzera_la_soglia_di_QUEL_mercato(self):
        """⚠️ «Offerte attese» è un numero DI UN MERCATO: le 166 dell'Italia
        appiccicate a una riga ormai francese farebbero dire al riaggancio
        «ne sono attese 166» di un mercato che non le ha mai avute."""
        self.it.sudo().write({"storefront": "fr"})
        self.assertEqual(self.it.sudo().offerte_attese, 0)
        self.assertEqual(self.de.sudo().offerte_attese, 166)

    def test_10_cambiare_il_gruppo_di_spedizione_NON_richiude(self):
        """Non tutto è identità: il gruppo di spedizione si corregge senza
        rimettere in discussione cosa esiste su Kaufland."""
        self.it.sudo().write({"shipping_group_id": "999999"})
        self.assertTrue(self.it.sudo().riagganciato)
