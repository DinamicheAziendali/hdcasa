# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il trasporto verso Cdiscount (tecnicamente Octopia): una chiamata, tre
intestazioni, e un errore che dice QUALE campo.

Il `trasporto` si passa da fuori, cosi' il client si prova senza rete
(tools/test_cdiscount_client.py inietta un finto). In Odoo il connettore passa
`TrasportoRequests`.

⚠️ `gettone` e' una FUNZIONE senza argomenti che rende un token valido, non
una stringa. Il client la chiama a ogni richiesta e non sa nulla di scadenze:
chi la scrive (il connettore) sa rinnovare il token guardando
`cdiscount_token.serve_rinnovo`. E' esattamente cio' che tiene questo file
libero da Odoo — e che gli evita di duplicare la logica della scadenza, che
sta gia' in `cdiscount_token`.

Questo file NON importa Odoo.
"""
import json
import logging

_logger = logging.getLogger(__name__)

CDISCOUNT_URL = "https://api.octopia-io.net/seller/v2"
# OAuth2 `client_credentials` sul realm Keycloak `maas`. Qui sta solo
# l'indirizzo: chi chiede il token e ne calcola la scadenza e' il connettore,
# con `cdiscount_token`.
TOKEN_URL = ("https://auth.octopia-io.net/auth/realms/maas"
             "/protocol/openid-connect/token")
# Il canale di vendita francese. Verificato sul vero con
# `GET /sellers/subscriptions`.
CANALE_FRANCIA = "CDISFR"
# Quanto si aspetta una risposta prima di rinunciare.
ATTESA = 30
# Quanto dettaglio si tiene di una risposta d'errore. Finisce nel registro e a
# video: un corpo enorme riempirebbe la schermata delle operazioni.
MAX_DETTAGLIO = 500
# Il minimo che OGNI chiave del corpo d'errore si porta a casa, anche quando
# sono tante: sotto questa soglia il valore non si riconoscerebbe piu'.
MIN_PER_CHIAVE = 60
# ⚠️ Il tetto assoluto del dettaglio composto: dieci volte MAX_DETTAGLIO,
# tanto che nessun corpo d'errore ragionevole lo tocca mai. Serve al caso
# irragionevole, che pero' e' possibile: un pacchetto porta fino a 10.000
# schede, e se Octopia rifiutandolo rispondesse con UNA CHIAVE PER RIGA in
# errore, il dettaglio finirebbe in un registro del worker e in un campo di
# un record Odoo. Misurato senza tetto: 200 chiavi con nomi lunghi facevano
# 42.404 caratteri.
TETTO_DETTAGLIO = 4000
# ⚠️ IL TETTO DEL `title`, ed e' l'ultimo pezzo di `messaggio` che non ne
# aveva uno. Per RFC 9457 il `title` e' una frase breve e generica
# («Validation Failed»), quindi 200 caratteri sono larghissimi per l'uso
# vero — ma «per specifica e' breve» non e' un tetto, e questo testo finisce
# TALE E QUALE nel campo `motivo` di TUTTE le righe di un pacchetto rifiutato
# (fino a 10.000) e nel `message` del registro. Misurato senza tetto: un
# `title` da 100.000 caratteri faceva un messaggio da 100.031 e un
# `_motivo_stato` da 100.041, cioe' circa 1 GB su un pacchetto pieno. E' la
# stessa asimmetria gia' chiusa su `errors` (Compito 3) e su `status`
# (Compito 5).
MAX_TITOLO = 200
SEGNO_TAGLIO = "…"


def _stato_intero(valore):
    """Lo stato HTTP, sempre come numero intero.

    ⚠️ Non e' pignoleria: `integrations_core/connectors/base.py`,
    `_causa_incerta`, confronta lo stato con `>= 500` per distinguere un
    esito IGNOTO da un rifiuto certo, e pretende `.stato` intero. Uno stato
    nullo o testuale — quel che arriverebbe da un trasporto guasto —
    esploderebbe proprio nel caso «guasto» che quel metodo esiste per
    gestire. Uno stato incomprensibile vale 0: non e' andato bene, non e' una
    frenata, ed e' confrontabile.
    """
    try:
        return int(valore)
    except (TypeError, ValueError):
        return 0


def _taglia(testo, quota):
    """Un pezzo del messaggio, mai piu' lungo di `quota`, col segno del taglio.

    ⚠️ Sta in una funzione sola perche' **un solo pezzo senza tetto rende
    senza tetto il messaggio intero**: il `title` e' rimasto scoperto per due
    giri di correzione proprio perche' ogni pezzo si tagliava per conto suo,
    con la sua riga scritta a mano. Chi aggiunge un pezzo a `messaggio` passa
    da qui e non deve decidere niente.
    """
    if len(testo) <= quota:
        return testo
    return testo[:quota] + SEGNO_TAGLIO


def _dettaglio(resto):
    """Il resto del corpo d'errore, in JSON, troncato CHIAVE PER CHIAVE.

    ⚠️ Tagliare la stringa gia' serializzata — come fa Kaufland — sembra
    innocuo e non lo e': l'ordine delle chiavi lo decide il server, e i corpi
    RFC 9457 di Octopia portano `instance`, `detail` e identificativi di
    tracciamento. Misurato: un `traceId` di 455 caratteri davanti al
    dettaglio fa finire il messaggio a meta' di una parola e il motivo
    sparisce; con uno di 600 la chiave del dettaglio non compare proprio. E'
    la giornata persa davanti a 166 righe identiche, spostata cinquecento
    caratteri piu' in la'.

    Il comportamento, per intero:

    - ogni chiave ha la sua fetta di budget, `MAX_DETTAGLIO` diviso il numero
      delle chiavi e comunque non meno di `MIN_PER_CHIAVE`. Una chiave
      rumorosa si accorcia da sola e non puo' far sparire le altre;
    - ⚠️ la fetta vale anche per il NOME della chiave, non solo per il
      valore. I nomi sono la voce che cresce di piu' — misurato: 200 chiavi
      con nomi da 203 caratteri facevano 42.404 caratteri di messaggio, e i
      valori non c'entravano niente;
    - sopra `TETTO_DETTAGLIO` il dettaglio si chiude, e **la coda dice quante
      chiavi sono state scritte e quante sono rimaste fuori**. Il taglio qui
      e' sulla stringa composta — quello che avevamo scartato — ma non
      decapita nessuno: a monte ogni chiave ha gia' avuto la sua fetta, le
      prime arrivano intere, e quel che manca e' DICHIARATO. Un dettaglio
      tagliato in silenzio e' il difetto che questo compito esiste per
      evitare; uno che dichiara di esserlo e' informazione.

    ⚠️ Non si nomina nessuna chiave: il rimedio funziona senza sapere come si
    chiami quella del dettaglio, cosi' se Cdiscount la rinomina regge uguale.
    """
    if not resto:
        return ""
    quota = max(MAX_DETTAGLIO // len(resto), MIN_PER_CHIAVE)
    pezzi = []
    for chiave, valore in resto.items():
        nome = _taglia(str(chiave), quota)
        scritto = _taglia(json.dumps(valore, ensure_ascii=False,
                                     default=str), quota)
        pezzi.append("%s: %s" % (json.dumps(nome, ensure_ascii=False),
                                 scritto))
    intero = "{%s}" % ", ".join(pezzi)
    if len(intero) <= TETTO_DETTAGLIO:
        return intero
    # Il tetto e' scattato: si scrivono le chiavi che ci stanno, e la coda
    # dichiara quante ne mancano. La riserva e' calcolata sul caso peggiore
    # della coda, cosi' il risultato non supera MAI il tetto.
    totale = len(pezzi)
    riserva = len(_coda(totale, totale)) + 4
    scritti = []
    lunghezza = 0
    for pezzo in pezzi:
        aggiunta = len(pezzo) + (2 if scritti else 0)
        if lunghezza + aggiunta > TETTO_DETTAGLIO - riserva:
            break
        scritti.append(pezzo)
        lunghezza += aggiunta
    scritti.append(_coda(totale - len(scritti), totale))
    return "{%s}" % ", ".join(scritti)


def _coda(mancanti, totale):
    """La riga che dichiara il taglio. Sta in una funzione sola perche' la
    sua lunghezza si deve poter calcolare PRIMA di comporre il dettaglio."""
    return "%s e altre %d chiavi su %d non scritte" % (SEGNO_TAGLIO, mancanti,
                                                       totale)


class RispostaCdiscount:
    """Una risposta di Octopia, gia' letta: stato, corpo spacchettato, testo
    grezzo.

    Espone `.stato` (intero) e `.messaggio` (testo): e' il contratto che
    `_causa_incerta` di `integrations_core/connectors/base.py` si aspetta.

    ⚠️ **E espone `.prevolo`, che separa due cose che lo stato 0 confondeva.**
    Lo stato 0 vuol dire «non e' arrivata una risposta», e `_causa_incerta` lo
    legge — giustamente — come «la richiesta puo' essere arrivata lo stesso».
    Ma `chiama()` rende stato 0 anche per i due passi che vengono PRIMA della
    rete (il gettone non ottenuto, il carico non serializzabile), e li' non e'
    partito NIENTE: nessun byte ha lasciato questa macchina. Le due cose
    portano a decisioni opposte — su un esito incerto l'invio congela fino a
    10.000 righe e manda a RILEGGERE cosa c'e' sul marketplace, su un non-
    partito la cosa giusta e' semplicemente rimandare — e senza questa
    bandiera non c'e' modo di distinguerle guardando la risposta.
    """

    def __init__(self, stato, corpo, testo, prevolo=False):
        self.stato = _stato_intero(stato)
        self.corpo = corpo
        self.testo = testo or ""
        # ⚠️ Vero SOLO quando il guasto e' avvenuto prima di toccare la rete.
        # Un trasporto che rende 0 (rete caduta, tempo scaduto) NON lo alza:
        # la' la richiesta puo' essere partita davvero.
        self.prevolo = bool(prevolo)

    @property
    def ok(self):
        return 200 <= self.stato < 300

    @property
    def dati(self):
        """Il contenuto utile di una risposta ANDATA BENE.

        ⚠️ Su un rifiuto si rende None e basta. Senza questa guardia un 401
        con `{"error": "invalid_token"}` renderebbe quel dizionario come se
        fossero dati — vero in senso booleano — e i consumatori della casa
        sono scritti nella forma `for voce in (risposta.dati or []):`: su un
        errore itererebbero le CHIAVI del corpo d'errore contandole come
        righe, in silenzio. Chi vuole leggere il corpo di un rifiuto usa
        `.corpo`, che resta a disposizione.

        Andata bene: Octopia incarta gli elenchi in `data`, mentre le
        risposte a oggetto singolo — per esempio il numero di pacchetto di
        `POST /products-integration` — arrivano nude. Percio' si prende
        `data` quando la chiave c'e', e altrimenti il corpo stesso.
        """
        if not self.ok:
            return None
        if isinstance(self.corpo, dict):
            if "data" in self.corpo:
                return self.corpo["data"]
            return self.corpo
        if isinstance(self.corpo, list):
            return self.corpo
        return None

    @property
    def messaggio(self):
        """Il motivo, DETTAGLIO COMPRESO.

        ⚠️ Cdiscount usa corpi RFC 9457: il titolo e' generico («Validation
        Failed»), e QUALE campo e PERCHE' stanno nel resto del corpo. Tenere
        il solo titolo e' il difetto che su Kaufland e' costato una giornata
        davanti a 166 righe identiche.

        Non si nomina la chiave del dettaglio: si prende tutto quel che c'e'
        oltre il titolo, cosi' se Cdiscount la rinomina il dettaglio continua
        ad arrivare. ⚠️ Il troncamento **del dettaglio** e' per chiave (vedi
        `_dettaglio`); il `title` non passa di li' e ha un tetto suo, qui
        sotto.

        ⚠️ **Ogni pezzo ha il suo tetto, `title` compreso** (`MAX_TITOLO`):
        questo testo viene scritto tale e quale sul campo `motivo` di tutte le
        righe di un pacchetto rifiutato — fino a 10.000 — e nel `message` del
        registro. Un solo pezzo senza tetto basta a rendere senza tetto il
        tutto.
        """
        if isinstance(self.corpo, dict):
            base = _taglia(str(self.corpo.get("title") or "").strip(),
                           MAX_TITOLO)
            resto = {c: v for c, v in self.corpo.items()
                     if c not in ("title", "status")}
            if resto:
                return ("%s — %s" % (base, _dettaglio(resto))).strip(" —")
            if base:
                return base
        testo = (self.testo or "")[:300]
        if testo.strip():
            return testo
        # ⚠️ Un rifiuto a corpo VUOTO capita davvero: il 503 di un proxy non
        # scrive niente. Senza questa riga il motivo sarebbe una stringa
        # vuota, e nel registro si leggerebbe «Motivi: 1 × » — cioe' niente.
        # Lo stato e' l'unica cosa che abbiamo in mano: si scrive.
        return "nessun dettaglio (stato %s)" % self.stato


class TrasportoRequests:
    """Il trasporto vero. Unico punto che tocca la rete."""

    def chiama(self, metodo, uri, teste, corpo):
        try:
            # ⚠️ L'import sta DENTRO il try, non solo dentro il metodo:
            # `requests` c'e' nell'immagine Odoo ma non su ogni macchina, e
            # un ImportError che risale sarebbe esattamente il guasto che
            # l'import locale dichiara di evitare. Anche la lettura della
            # risposta sta qui dentro: una risposta mutilata non deve
            # propagare piu' di una connessione caduta.
            import requests

            risposta = requests.request(metodo, uri, headers=teste,
                                        data=corpo, timeout=ATTESA)
            return risposta.status_code, risposta.text, dict(risposta.headers)
        except Exception as errore:  # rete/timeout: non deve mai propagare
            # ⚠️ Stessa convenzione degli altri connettori della casa (vedi
            # marketplace_temu/connectors/temu_client.py, «rete/timeout: non
            # deve mai propagare», e marketplace_kaufland). Dentro Odoo
            # un'eccezione di rete che passa di qui diventa una traccia di
            # stack grezza al posto di una risposta, e il giro si interrompe
            # senza lasciare scritto niente.
            #
            # Lo stato 0 e' quello che `_stato_intero` gia' assegna a uno
            # stato incomprensibile, ed e' il valore su cui `_causa_incerta`
            # riconosce l'esito IGNOTO: la richiesta puo' essere arrivata lo
            # stesso, quindi non si ritenta alla cieca. Il motivo viaggia nel
            # testo, cosi' `messaggio` lo restituisce per esteso invece di un
            # generico «errore di rete».
            _logger.warning("Cdiscount %s %s: errore di rete: %s",
                            metodo, uri, errore)
            return 0, str(errore), {}


class CdiscountClient:
    def __init__(self, venditore, canale, trasporto, gettone,
                 base_url=CDISCOUNT_URL):
        self._venditore = venditore
        self._canale = canale
        self._trasporto = trasporto
        # ⚠️ Una funzione, non una stringa: vedi il perche' in cima al file.
        self._gettone = gettone
        self.base_url = (base_url or "").rstrip("/")

    def _teste(self):
        """Le intestazioni obbligatorie.

        ⚠️ Sono TRE, non due: senza `SalesChannelId` alcune chiamate
        rispondono 400 dicendolo per nome. Il token si chiede al `gettone`
        adesso, non alla costruzione del client: fra un giro e l'altro puo'
        essere stato rinnovato.
        """
        return {"Authorization": "Bearer %s" % self._gettone(),
                "SellerId": self._venditore,
                "SalesChannelId": self._canale,
                "Accept": "application/json"}

    def chiama(self, metodo, percorso, corpo=None):
        """Una chiamata. `percorso` comincia con '/' e puo' portare la
        stringa di ricerca gia' dentro.

        ⚠️ Non solleva MAI, e la promessa vale anche per i due passi che
        vengono prima della rete: chiedere il token e serializzare il carico.
        Sono due percorsi veri — il `gettone` del connettore fa una POST
        OAuth2 che puo' cadere, e un carico costruito da dati Odoo puo'
        portarsi dentro una data o un Decimal. Un'eccezione da qui farebbe
        morire l'invio di un lotto con una traccia di stack invece di una
        riga fallita. Tutto torna dentro una `RispostaCdiscount` a stato 0,
        cioe' esito IGNOTO per `_causa_incerta`.

        ⚠️ **Ma i due passi PRIMA della rete rendono `prevolo=True`**, e la
        differenza vale fino a 10.000 schede. Uno stato 0 dal trasporto vuol
        dire «non so se sia arrivata»; uno stato 0 di qui sopra vuol dire
        «non e' partito niente», perche' nessun byte ha lasciato questa
        macchina. Chi decide cosa fare delle righe deve poterli separare: sul
        primo si congela e si va a guardare cosa c'e' sul marketplace, sul
        secondo si rimanda e basta. Vedi `RispostaCdiscount`.
        """
        uri = self.base_url + percorso
        try:
            teste = self._teste()
        except Exception as errore:  # il token non e' arrivato
            _logger.warning("Cdiscount %s %s: token non ottenuto: %s",
                            metodo, uri, errore)
            # ⚠️ `prevolo`: qui non e' partito niente. Il token si chiede
            # PRIMA di comporre la richiesta, e senza di lui la richiesta non
            # viene nemmeno costruita.
            return RispostaCdiscount(
                0, None, "il token non è arrivato: %s" % errore,
                prevolo=True)
        byte = None
        if corpo is not None:
            # ⚠️ Il corpo si serializza UNA volta sola, qui, e al trasporto
            # si consegnano i BYTE: lasciare a chi spedisce la scelta della
            # codifica fa arrivare mojibake al primo trattino lungo o simbolo
            # € — cose ordinarie nei titoli italiani e francesi.
            #
            # ⚠️ `default=str` come in `messaggio`: date e Decimal arrivano
            # normalmente dai campi Odoo, e non sono un buon motivo per far
            # fallire un lotto intero. Quel che resta si cattura lo stesso —
            # un riferimento circolare, per dire — e il motivo dice
            # CHIARAMENTE che la colpa e' nostra, non del marketplace:
            # cercarla dalla parte di Cdiscount sarebbe tempo buttato.
            try:
                byte = json.dumps(corpo, ensure_ascii=False,
                                  default=str).encode("utf-8")
            except Exception as errore:
                _logger.warning("Cdiscount %s %s: carico non serializzabile "
                                "(difetto nostro): %s", metodo, uri, errore)
                # ⚠️ `prevolo` anche qui, e per lo stesso motivo: il corpo
                # non esiste ancora, quindi non c'e' niente che possa essere
                # arrivato a Cdiscount.
                return RispostaCdiscount(
                    0, None, "il carico non è serializzabile: è un difetto "
                    "nostro, non una risposta di Cdiscount — %s" % errore,
                    prevolo=True)
            # Il tipo di contenuto si dichiara solo quando un contenuto c'e',
            # come fa Kaufland.
            teste["Content-Type"] = "application/json"
        stato, testo, _teste_risposta = self._trasporto.chiama(
            metodo, uri, teste, byte)
        try:
            spacchettato = json.loads(testo) if testo else None
        except ValueError:
            # ⚠️ Un corpo illeggibile non fa esplodere il client: e' la
            # pagina d'errore di un proxy, e il testo grezzo resta a
            # disposizione di `messaggio`.
            spacchettato = None
        return RispostaCdiscount(stato, spacchettato, testo)
