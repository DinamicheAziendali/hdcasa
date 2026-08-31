# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Un canale che rompe la BANCA DATI non deve portarsi via gli altri.

⚠️ Questa e' la prova che mancava, e la differenza con quelle che esistono gia'
non e' un dettaglio.

I 75 controlli in `tools/test_isolamento_canali.py` e
`tools/test_connettori_savepoint.py` fanno girare il codice vero contro un
cursore **finto**, che *imita* una transazione abortita. Dicono una cosa sola,
preziosa: **«il codice chiede il savepoint nel punto giusto»**. Non dicono che
PostgreSQL faccia la sua parte — ed e' esattamente li' che stava il difetto
originale: **catturare un errore del database in Python NON salva la
transazione**. Postgres la lascia ABORTITA, e da li' in poi rifiuta qualunque
istruzione, letture comprese.

Qui il guasto e' **vero**: una divisione per zero eseguita da PostgreSQL. E le
tre cose che si verificano sono le tre che prima erano false:

1. il canale DOPO quello guasto viene comunque servito;
2. il lavoro del canale PRIMA e' ancora li' — prima il salvataggio finale
   diventava un annullamento silenzioso che se lo portava via;
3. la riga d'errore del canale guasto **esiste davvero** nel registro — prima
   non si scriveva, e i conteggi dicevano che era tutto a posto.

Correzione del 2026-08-27, mai esercitata dentro un Odoo fino al 2026-08-30.
"""
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


class ConnettoreFinto(object):
    """Sta al posto del connettore vero: uno lavora, uno rompe il database."""

    def __init__(self, canale, rompe):
        self.canale = canale
        self.rompe = rompe

    def generate_stock_feed(self):
        if self.rompe:
            # ⚠️ Un errore che viene da POSTGRESQL, non un RuntimeError
            # qualunque: e' tutta la differenza. Un'eccezione Python lascia la
            # transazione sana e il giro proseguirebbe anche senza savepoint —
            # cioe' la prova passerebbe anche su un codice rotto.
            self.canale.env.cr.execute("SELECT 1 / 0")
            return True
        # Il «lavoro» di un canale sano: qualcosa che resti scritto e si possa
        # ritrovare dopo.
        self.canale.write({
            "stock_feed_content": "feed di %s" % self.canale.name,
            "stock_feed_generated_at": fields.Datetime.now(),
        })
        # ⚠️ IL FLUSH NON E' UN DETTAGLIO, e me l'ha insegnato il giro rosso del
        # 2026-08-30: senza, il `write` resta nella cache dell'ORM e non tocca
        # il database. Due controlli qui sotto passavano ALLORA ANCHE COL
        # DIFETTO RIMESSO — cioe' non proteggevano niente. Con il flush il
        # lavoro finisce davvero in SQL, prima che il canale seguente abortisca
        # la transazione.
        self.canale.env.flush_all()
        return True


@tagged("post_install", "-at_install", "centrivo_core")
class TestIsolamentoCanaliVero(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Canale = cls.env["centrivo.channel"]
        codice = Canale._get_connector_selection()[0][0]
        # ⚠️ L'ordine conta: il guasto sta IN MEZZO. Con il guasto in fondo la
        # prova passerebbe anche senza isolamento, perche' non ci sarebbe
        # nessun canale «dopo» da servire.
        cls.primo, cls.guasto, cls.terzo = [
            Canale.create({"name": nome, "connector_code": codice,
                           "company_id": cls.env.company.id})
            for nome in ("Primo (sano)", "Secondo (rompe il database)",
                         "Terzo (sano)")]
        cls.tutti = cls.primo | cls.guasto | cls.terzo

    def _gira(self):
        """Il giro vero, col connettore sostituito canale per canale."""
        def finto(canale):
            return ConnettoreFinto(canale, rompe=(canale.id == self.guasto.id))

        with patch.object(type(self.tutti), "_get_connector", finto):
            self.tutti.action_generate_stock_feed()

    def _letto_dal_database(self, canale):
        """Il feed di quel canale letto in SQL, non dalla cache dell'ORM.

        ⚠️ Leggere `canale.stock_feed_content` non basta: la cache
        restituirebbe il valore scritto anche se non ha mai raggiunto il
        database. La domanda vera e' «c'e' finito davvero?».
        """
        self.env.cr.execute(
            "SELECT stock_feed_content FROM centrivo_channel WHERE id = %s",
            (canale.id,))
        riga = self.env.cr.fetchone()
        return riga[0] if riga else None

    def _righe_registro(self, canale):
        return self.env["centrivo.job.log"].sudo().search([
            ("channel_id", "=", canale.id),
            ("operation", "=", "export_stock_feed"),
        ])

    # ------------------------------------------------------------------
    def test_01_il_giro_non_muore_sul_canale_guasto(self):
        """Il giro arriva in fondo, invece di morire sul secondo canale."""
        self._gira()   # non deve sollevare

    def test_02_il_canale_DOPO_quello_guasto_viene_servito(self):
        """⚠️ IL CONTROLLO CHE VALE PIU' DI TUTTI.

        Prima, l'errore del database usciva dal ciclo: il terzo canale quel
        giro **non esisteva**. Ed e' il modo piu' costoso di sbagliare, perche'
        non lo dice nessuno: il canale semplicemente non viene aggiornato.
        """
        self._gira()
        self.assertTrue(
            self._letto_dal_database(self.terzo),
            "Il canale dopo quello guasto deve essere stato servito, e il suo "
            "lavoro dev'essere NEL DATABASE: se e' vuoto, il guasto del "
            "secondo si e' portato via il giro.")

    def test_03_il_lavoro_del_canale_PRIMA_sopravvive(self):
        """Il guasto non annulla cio' che era gia' andato bene.

        ⚠️ Prima il salvataggio finale si trasformava in un annullamento
        silenzioso: la transazione era abortita, e il commit portava via anche
        i canali gia' serviti. Mentre i conteggi dicevano che era tutto a
        posto.
        """
        self._gira()
        scritto = self._letto_dal_database(self.primo)
        self.assertTrue(
            scritto,
            "Il lavoro del primo canale dev'essere ancora NEL DATABASE dopo "
            "il guasto del secondo.")
        self.assertIn("Primo", scritto)

    def test_04_la_transazione_resta_UTILIZZABILE(self):
        """Dopo il guasto si deve poter ancora scrivere.

        E' la prova piu' vicina all'osso: su una transazione abortita
        PostgreSQL rifiuta **qualunque** istruzione. Se questa scrittura passa,
        vuol dire che il ritorno al savepoint ha davvero rimesso in piedi la
        transazione — cosa che i banchi col cursore finto non possono provare.
        """
        self._gira()
        self.env["centrivo.channel"].create({
            "name": "Nato dopo il guasto",
            "connector_code": self.primo.connector_code,
            "company_id": self.env.company.id,
        })

    def test_05_l_errore_del_canale_guasto_e_SCRITTO(self):
        """La riga di registro del guasto esiste davvero.

        ⚠️ Prima non si scriveva: il gestore d'errore leggeva il nome del
        canale — una lettura SQL — su una transazione gia' abortita, e falliva
        a sua volta. Restava un giro che non diceva niente a nessuno.
        """
        self._gira()
        righe = self._righe_registro(self.guasto)
        self.assertEqual(
            len(righe), 1,
            "Il canale guasto deve lasciare UNA riga di registro: senza, il "
            "guasto e' invisibile e i conteggi dicono che e' andato tutto "
            "bene.")
        self.assertEqual(righe.result, "error")

    def test_06_i_canali_sani_non_lasciano_righe_di_errore(self):
        """E il registro non si riempie di allarmi per chi ha lavorato bene.

        Serve a distinguere «l'isolamento funziona» da «tutto fallisce e
        qualcuno lo scrive»: senza questo controllo, un giro in cui rompono
        tutti e tre supererebbe le prove qui sopra tranne una.
        """
        self._gira()
        self.assertFalse(self._righe_registro(self.primo))
        self.assertFalse(self._righe_registro(self.terzo))
