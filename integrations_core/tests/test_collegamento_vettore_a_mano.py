# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Collegare un vettore NUOVO dalla schermata «Vettori» non si poteva fare.

⚠️ Segnalato da Angelo il 2026-09-07: apre Integrations → Corrieri e vettori →
Vettori, aggiunge la riga «DPD», sceglie DPD nella tendina «Seleziona vettore»,
mette DPD come corriere, salva — e Odoo risponde *«Indica il vettore da
collegare: seleziona un vettore nella tendina 'Seleziona vettore'»*. Cioè gli
chiede proprio la cosa che aveva appena fatto.

Il difetto non era mai emerso perché le 5 righe in produzione le ha scritte la
MIGRAZIONE, che compila le chiavi durevoli direttamente: la strada
dell'interfaccia — l'unica che un cliente userà — non era mai stata percorsa.

Causa: l'inverse della tendina scriveva le tre chiavi durevoli con tre
assegnazioni separate. In Odoo ogni assegnazione su un record già salvato è una
write() a sé, e ogni write() rivaluta i vincoli: alla PRIMA (`source_model`) il
record aveva ancora `source_res_id` vuoto, e il vincolo — che pretende
giustamente le due chiavi INSIEME — bocciava una riga che stava per essere
completata la riga dopo.
"""
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "centrivo_core")
class TestCollegamentoVettoreAMano(TransactionCase):

    def setUp(self):
        super().setUp()
        prodotto = self.env["product.product"].create(
            {"name": "Spese di spedizione", "type": "service"})
        self.vettore = self.env["delivery.carrier"].create(
            {"name": "DPD", "product_id": prodotto.id})
        self.corriere = self.env.ref("integrations_core.carrier_brand_dpd")

    def _come_fa_la_schermata(self):
        """Esattamente i valori che l'interfaccia manda al salvataggio.

        La schermata mostra solo la tendina e il corriere: `source_model`,
        `source_res_id` e `source_display` non sono in vista e non vengono
        inviati — li deve ricavare l'inverse dalla scelta in tendina.
        """
        return self.env["centrivo.carrier.source"].create({
            "source_record_key": str(self.vettore.id),
            "brand_id": self.corriere.id,
        })

    # ------------------------------------------------------------------
    def test_01_si_collega_un_vettore_scegliendolo_dalla_tendina(self):
        """⚠️ È il gesto di Angelo: scegli il vettore, scegli il corriere, salva."""
        riga = self._come_fa_la_schermata()
        self.assertEqual(riga.brand_id, self.corriere)

    def test_02_e_la_riga_salvata_sa_qual_e_il_vettore(self):
        """Le tre chiavi durevoli devono essere scritte, non solo il corriere:
        è con quelle che le spedizioni ritrovano il collegamento."""
        riga = self._come_fa_la_schermata()
        self.assertEqual(riga.source_model, "delivery.carrier")
        self.assertEqual(riga.source_res_id, self.vettore.id)
        self.assertEqual(riga.source_display, "DPD")

    def test_03_la_colonna_Vettore_si_riempie_da_sola(self):
        """La seconda colonna della lista è di sola lettura: se non la
        compilasse l'inverse, resterebbe vuota per sempre."""
        self.assertEqual(self._come_fa_la_schermata().source_display, "DPD")

    def test_04_e_la_riga_si_ritrova_dalle_spedizioni(self):
        """Il collegamento serve a questo: partire dal vettore di un
        trasferimento e arrivare al corriere. Se le chiavi non ci sono, la
        riga esiste ma non la trova nessuno."""
        trovata = self.env["centrivo.carrier.source"].resolve_brand(
            "delivery.carrier", self.vettore.id, self.env.company)
        self.assertFalse(trovata, "prima del collegamento non deve trovare nulla")
        self._come_fa_la_schermata()
        trovata = self.env["centrivo.carrier.source"].resolve_brand(
            "delivery.carrier", self.vettore.id, self.env.company)
        self.assertEqual(trovata.brand_id, self.corriere)

    # ------------------------------------------------------------------
    def test_05_una_riga_SENZA_vettore_resta_vietata(self):
        """Il vincolo non va indebolito: senza vettore la riga è inservibile
        (nessuna spedizione la troverebbe) e va ancora rifiutata."""
        with self.assertRaises(ValidationError):
            self.env["centrivo.carrier.source"].create(
                {"brand_id": self.corriere.id})

    def test_06_e_una_tendina_che_punta_al_nulla_non_scrive_niente(self):
        """Vettore cancellato tra la scelta e il salvataggio: meglio il
        rifiuto della riga a metà."""
        fantasma = self.env["delivery.carrier"].browse(999999)
        self.assertFalse(fantasma.exists())
        with self.assertRaises(ValidationError):
            self.env["centrivo.carrier.source"].create({
                "source_record_key": str(fantasma.id),
                "brand_id": self.corriere.id,
            })
