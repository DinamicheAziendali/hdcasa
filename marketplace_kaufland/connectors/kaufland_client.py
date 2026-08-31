# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il trasporto verso Kaufland: una chiamata firmata, e la paginazione.

Il `trasporto` si passa da fuori, cosi' il client si prova senza rete
(tools/test_kaufland_client.py inietta un finto). In Odoo il connettore passa
`TrasportoRequests`.

Questo file NON importa Odoo.
"""
import json
import logging
import time

# ⚠️ Doppia forma di import, e serve DAVVERO: dentro Odoo questi file sono un
# pacchetto (import relativo), mentre i test di tools/ mettono la sola cartella
# `connectors/` sul percorso (import assoluto). Una forma sola rompe l'altra
# meta': quella relativa fa fallire i test, quella assoluta fa fallire
# l'installazione del modulo.
try:
    from .kaufland_firma import intestazioni
    from .kaufland_risposta import messaggio as _messaggio
except ImportError:  # eseguito fuori da Odoo, dai test di tools/
    from kaufland_firma import intestazioni
    from kaufland_risposta import messaggio as _messaggio

_logger = logging.getLogger(__name__)

KAUFLAND_URL = "https://sellerapi.kaufland.com/v2"
DIMENSIONE_PAGINA = 100
# Quanto si aspetta una risposta prima di rinunciare.
ATTESA = 30


class LetturaInterrotta(Exception):
    """Un elenco si e' fermato a meta' per un errore.

    ⚠️ Esiste perche' un generatore che smette di produrre righe e'
    indistinguibile da uno che ha finito. Senza un segnale esplicito, mezzo
    elenco verrebbe dichiarato completo — e i numeri sbagliati sembrano buoni.
    """


def _stato_intero(valore):
    """Lo stato HTTP, sempre come numero intero.

    ⚠️ Non e' pignoleria: `kaufland_catalogo.stato_scheda` confronta lo stato
    con `200 <= stato_http` senza difese. Uno stato nullo o testuale — quel
    che arriverebbe da un trasporto guasto — esploderebbe con TypeError
    proprio nel caso «guasto» che quel modulo esiste per gestire. Uno stato
    incomprensibile vale 0: non e' andato bene, non e' una frenata, ed e'
    confrontabile.
    """
    try:
        return int(valore)
    except (TypeError, ValueError):
        return 0


def _numero(valore):
    """Un intero, oppure None se il valore non e' un numero.

    Serve per i conteggi che Kaufland dichiara: confrontare un numero con una
    stringa esplode, e un totale illeggibile deve semplicemente non essere
    confrontato — non far finta che sia zero. `True` non e' un conteggio: si
    scarta, perche' `int(True)` darebbe 1.
    """
    if valore is None or isinstance(valore, bool):
        return None
    try:
        return int(valore)
    except (TypeError, ValueError):
        return None


class RispostaKaufland:
    def __init__(self, stato, corpo, testo, teste=None):
        self.stato = _stato_intero(stato)
        self.corpo = corpo
        self.testo = testo or ""
        # ⚠️ Le intestazioni servono davvero: `POST /units/` risponde con
        # corpo VUOTO e l'indirizzo della nuova offerta in `Location`.
        # Buttarle via significherebbe perdere l'identificativo dell'offerta
        # creata, e ricrearla a ogni giro.
        self.teste = dict(teste or {})

    @property
    def ok(self):
        return 200 <= self.stato < 300

    @property
    def troppe_chiamate(self):
        """Il tetto e' 111 chiamate al secondo per venditore. Chi chiama
        rallenta: insistere peggiora la situazione."""
        return self.stato == 429

    @property
    def dati(self):
        if isinstance(self.corpo, dict):
            return self.corpo.get("data")
        return None

    @property
    def paginazione(self):
        if isinstance(self.corpo, dict):
            return self.corpo.get("pagination") or {}
        return {}

    @property
    def messaggio(self):
        return _messaggio(self.stato, self.corpo, self.testo)


class TrasportoRequests:
    """Il trasporto vero. Unico punto che tocca la rete."""

    def chiama(self, metodo, uri, teste, corpo):
        # `requests` c'e' nell'immagine Odoo. Import locale, come negli altri
        # connettori: cosi' questo file resta importabile — e provabile — su
        # una macchina che `requests` non ce l'ha.
        import requests

        try:
            risposta = requests.request(metodo, uri, headers=teste,
                                        data=corpo, timeout=ATTESA)
        except Exception as errore:  # rete/timeout: non deve mai propagare
            # ⚠️ Stessa convenzione degli altri connettori della casa (vedi
            # marketplace_temu/connectors/temu_client.py). Dentro Odoo
            # un'eccezione di rete che passa di qui diventa una traccia di
            # stack grezza al posto di una risposta, e chi itera `pagine()`
            # non riceve nemmeno `LetturaInterrotta` — l'unico segnale che
            # dice «elenco incompleto, non fidarti dei numeri».
            #
            # Lo stato 0 e' quello che `_stato_intero` gia' assegna a uno
            # stato incomprensibile: non e' andato bene, non e' una frenata,
            # ed e' confrontabile. Il motivo viaggia nel testo, cosi'
            # `kaufland_risposta.messaggio` lo restituisce per esteso invece
            # di un generico «errore di rete».
            _logger.warning("Kaufland %s %s: errore di rete: %s",
                            metodo, uri, errore)
            return 0, str(errore), {}
        return risposta.status_code, risposta.text, dict(risposta.headers)


class KauflandClient:
    def __init__(self, chiave, segreto, trasporto, base_url=KAUFLAND_URL,
                 orologio=time.time):
        self._chiave = chiave
        self._segreto = segreto
        self._trasporto = trasporto
        self.base_url = (base_url or "").rstrip("/")
        self._orologio = orologio

    def chiama(self, metodo, percorso, corpo=None):
        """Una chiamata firmata. `percorso` comincia con '/' e puo' portare la
        stringa di ricerca gia' dentro.

        ⚠️ Il corpo si serializza UNA VOLTA e si firma quella stringa, poi si
        spedisce la stessa: riserializzare fra la firma e l'invio produce una
        firma che non torna, e l'errore sembra un problema di credenziali.
        """
        uri = self.base_url + percorso
        testo_corpo = ""
        if corpo is not None:
            testo_corpo = json.dumps(corpo, ensure_ascii=False)
        # ⚠️ L'orario va troncato a intero QUI: kaufland_firma non lo fa, e un
        # orologio con i decimali darebbe "1756200000.75" — che Kaufland
        # rifiuta, lamentando credenziali non valide.
        quando = int(self._orologio())
        teste = intestazioni(metodo, uri, testo_corpo, quando, self._chiave,
                             self._segreto)
        # ⚠️ Al trasporto si consegnano i BYTE, non la stringa: la firma e'
        # calcolata su `testo.encode()`, cioe' UTF-8, e lasciare a chi spedisce
        # la scelta della codifica fa divergere i byte firmati dai byte spediti
        # al primo trattino lungo o simbolo € — cose ordinarie nei titoli
        # italiani e tedeschi. Cosi' la coincidenza e' dimostrabile.
        stato, testo, teste_risposta = self._trasporto.chiama(
            metodo, uri, teste,
            testo_corpo.encode("utf-8") if testo_corpo else None)
        try:
            spacchettato = json.loads(testo) if testo else None
        except ValueError:
            spacchettato = None
        return RispostaKaufland(stato, spacchettato, testo, teste_risposta)

    def pagine(self, percorso, dimensione=DIMENSIONE_PAGINA):
        """Le pagine di un elenco, una alla volta.

        Si ferma quando una pagina torna vuota E i conti tornano. In ogni
        altro caso solleva `LetturaInterrotta`: vedi il perche' sulla classe.
        """
        unione = "&" if "?" in percorso else "?"
        scarto = 0
        dichiarate = None
        while True:
            risposta = self.chiama(
                "GET", "%s%slimit=%s&offset=%s" % (percorso, unione,
                                                   dimensione, scarto))
            if not risposta.ok:
                raise LetturaInterrotta(
                    "L'elenco si e' fermato dopo %s righe: HTTP %s — %s"
                    % (scarto, risposta.stato, risposta.messaggio))
            # ⚠️ Un 200 con corpo illeggibile NON e' la fine dell'elenco:
            # e' la pagina d'errore di un proxy o di un gateway, che risponde
            # HTML con stato 200 — e succede. Senza questo controllo `dati`
            # varrebbe None, le righe zero, e mezzo elenco verrebbe dichiarato
            # completo: esattamente il guasto che questa classe esiste per
            # impedire. Il testo c'era: non e' una pagina vuota, e' una
            # risposta che non si e' capita.
            if risposta.corpo is None and risposta.testo:
                raise LetturaInterrotta(
                    "L'elenco si e' fermato dopo %s righe: risposta HTTP %s "
                    "illeggibile — %s" % (scarto, risposta.stato,
                                          risposta.messaggio))
            totale = _numero(risposta.paginazione.get("total"))
            if totale is not None:
                dichiarate = totale
            righe = risposta.dati or []
            if not righe:
                break
            yield righe
            scarto += len(righe)
        # ⚠️ L'ultima parola e' dei conti: Kaufland dichiara quante righe ci
        # sono, e chiudere con meno significa che per strada se ne sono perse.
        # Una pagina vuota puo' essere la fine legittima dell'elenco oppure un
        # buco: il totale dichiarato e' l'unico modo di distinguerli.
        if dichiarate is not None and scarto < dichiarate:
            raise LetturaInterrotta(
                "L'elenco si e' fermato a %s righe delle %s dichiarate da "
                "Kaufland" % (scarto, dichiarate))
