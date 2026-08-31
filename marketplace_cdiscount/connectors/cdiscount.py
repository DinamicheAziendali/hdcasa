# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Il connettore Cdiscount (Octopia): la parte che tocca Odoo.

La logica pura sta nei file accanto — il gettone, il trasporto, la forma di una
scheda, la lettura di un rapporto, la lettura del file dei contenuti — e si
prova senza Odoo con i test di `tools/`. Qui c'e' l'unica cosa che quei file
non possono fare: **parlare col marketplace vero e scrivere in banca dati**.

⚠️ **LA REGOLA CHE GOVERNA TUTTO QUESTO FILE.** `POST /products-integration`
accetta fino a 10.000 schede e risponde con un numero di pacchetto. L'esito
non torna subito: si va a ripescare dopo, e **scade in tre giorni**. Il numero
si scrive **nell'istante in cui arriva**, nel proprio savepoint, **prima di
qualunque altra cosa**. Se il processo muore un istante dopo, Cdiscount sta
lavorando un pacchetto di cui non sappiamo il numero: l'esito e' perso, e non
sapremo nemmeno cosa c'era dentro — non una scheda, fino a diecimila.

Le altre tre regole, e sono le stesse di `marketplace_kaufland`:

1. **Un giro per volta** (`_prendi_il_turno`, nella classe base).
2. **Su un esito IGNOTO ci si ferma e NON si ritenta.** Un pacchetto puo'
   essere stato preso lo stesso: rimandarlo raddoppierebbe il catalogo. Il
   messaggio dice che va **riletto** (`GET /products` elenca le schede
   nostre), non rimandato.
3. **Non si conta cio' che il rollback si porta via.**

⚠️ Le chiavi del corpo (`products`, `packageId`) vengono dalla
DOCUMENTAZIONE e non dal vero: vanno riconfermate alla prima spedizione. La
ricognizione del 2026-08-25 ha gia' trovato, per le offerte, un elenco di
campi che sembrava JSON ed era lo schema XML. Ovunque si e' scelta la forma
che sbaglia dalla parte prudente — un nome che non torna produce «numero non
conosciuto», cioe' una fermata rumorosa, mai un falso successo.
"""
import json
import logging
import re
import time
from datetime import timedelta
from urllib.parse import quote, urlencode

from odoo import _, fields
from odoo.exceptions import UserError

from odoo.addons.integrations_core.connectors.base import (
    MarketplaceConnector,
    register_connector,
)

from .cdiscount_client import (
    ATTESA,
    CDISCOUNT_URL,
    TOKEN_URL,
    CdiscountClient,
    TrasportoRequests,
)
from .cdiscount_rapporto import (
    CHIAVI_CODICE,
    IN_LAVORAZIONE,
    PRONTO,
    RIFIUTATO,
    RIUSCITO,
    _codice,
    _e_scalare,
    leggi_rapporto,
    riconcilia,
)
from .cdiscount_schede import (
    MAX_PER_PACCHETTO,
    _chi,
    _entro_il_limite,
    _testo,
    corpo_scheda,
    pacchetti,
)
from .cdiscount_token import scadenza, serve_rinnovo

# ⚠️ Il vocabolario degli stati di una scheda si prende DA DOVE VIVE, non si
# riscrive qui. Due copie della stringa «in_attesa» che divergono per una
# lettera farebbero fallire la scrittura sulla Selection — oppure, peggio,
# passerebbero e lascerebbero un valore che nessun filtro intercetta. Stessa
# ragione per `CON_CONTENUTI`: e' IL dominio che dice quali schede sono
# componibili, e lo usano in tre (la procedura del file, questo invio, la
# pagina del Compito 12).
from ..models.cdiscount_scheda import (
    CON_CONTENUTI,
    IN_ATTESA,
    SCONOSCIUTO_SCHEDA,
)
# ⚠️ Stessa ragione, un piano piu' su: gli stati e il tipo di un PACCHETTO si
# prendono da dove vivono. Una stringa storta qui non solleva niente — la
# `search` semplicemente non trova, e il raccoglitore smette di raccogliere in
# silenzio.
from ..models.cdiscount_pacchetto import (
    APERTO,
    ORE_AVVISO,
    RACCOLTO,
    SCADUTO,
    TIPO_SCHEDE,
)

_logger = logging.getLogger(__name__)

# Le due chiamate di questo compito. La seconda non si fa da qui: si NOMINA
# nei messaggi, perche' e' quella con cui una persona va a vedere cosa c'e'
# davvero la' fuori quando un esito e' ignoto.
API_SCHEDE = "/products-integration"
API_SCHEDE_NOSTRE = "/products"
# ⚠️ Il rapporto di UN pacchetto, e il numero va nel PERCORSO, non in una
# stringa di ricerca. Anche questo nome viene dalla documentazione (vedi
# l'avvertenza in testa al file), ma la forma non e' indifferente: se il nome
# di un parametro di ricerca fosse sbagliato, un server indulgente potrebbe
# rispondere con l'elenco di TUTTI i rapporti o con l'ultimo — cioe' col
# rapporto di un ALTRO pacchetto, letto come se fosse il nostro. Un percorso
# sbagliato risponde 404, che qui si legge «non lo so»: nessun verdetto,
# nessuna scrittura, e il pacchetto resta aperto.
API_RAPPORTI = "/products-integration-reports"
# La chiave sotto cui viaggiano le schede nel corpo. ⚠️ Documentazione, non
# vero: vedi l'avvertenza in testa al file.
CHIAVE_SCHEDE = "products"

# La finestra dell'esito. ⚠️ Non e' una scelta nostra: e' il tempo dopo il
# quale Cdiscount non ha piu' niente da dirci su quel pacchetto.
GIORNI_ESITO = 3

# ⚠️ IL TIPO DI ATTIVITA' CON CUI SI AVVISA UNA PERSONA, ed e' il «Da fare»
# nativo di Odoo — lo stesso identico xmlid che questa casa usa gia' in tre
# posti (`centrivo_shipment_tracking` per gli alert SLA, `marketplace_
# bricobravo` per un ordine non importato, `marketplace_temu` per il gettone
# in scadenza). Non e' un tipo nostro apposta: un tipo di attivita' inventato
# qui non comparirebbe nei filtri che una persona ha gia' imparato a usare.
#
# ⚠️ E il modulo `mail` C'E': `integrations_core` lo dichiara fra le sue
# `depends` (per le attivita' sugli ordini in errore, TASK_70), e
# `marketplace_cdiscount` dipende da `integrations_core`. Il manifesto di
# questo modulo lo nomina lo stesso, esplicitamente: una dipendenza
# transitiva sparisce il giorno in cui il modulo di mezzo smette di averne
# bisogno, e a sparire sarebbe l'unico avviso che questa consegna produce.
ATTIVITA_TODO = "mail.mail_activity_data_todo"

# Quanto payload si conserva nel registro: un corpo di 10.000 schede
# renderebbe illeggibile la schermata delle operazioni (e la riempirebbe di
# megabyte). Stesso valore di Kaufland.
MAX_PAYLOAD = 2000

# ⚠️ IL TEMPO CHE UN GIRO HA, E PERCHE' QUI CONTA PIU' CHE ALTROVE.
# `config/odoo.conf` dichiara `limit_time_real = 120`: una richiesta web che
# dura di piu' viene UCCISA dal worker, e un worker ucciso annulla l'INTERA
# transazione — savepoint compresi. Su Kaufland questo faceva perdere gli
# identificativi di offerte gia' nate (165 orfane il 2026-08-25). Qui farebbe
# perdere il NUMERO DI UN PACCHETTO GIA' PRESO, cioe' l'esito di fino a
# 10.000 schede, e senza appello: dopo tre giorni non esiste piu' nessun modo
# di sapere cosa Cdiscount ne abbia fatto.
LIMITE_WORKER = 120
# Quel che si lascia alla chiusura del giro (i conti e il registro) dopo
# l'ultima chiamata.
MARGINE_CHIUSURA = 15
# ⚠️ Si CALCOLA e non si scrive a mano: il controllo avviene PRIMA di una
# chiamata, e quella chiamata puo' durare fino ad `ATTESA` secondi. Un numero
# scritto a mano si disallineerebbe in silenzio il giorno in cui `ATTESA`
# cambia, e il caso peggiore sfonderebbe `limit_time_real` producendo proprio
# il pacchetto senza numero che questo file esiste per evitare.
SECONDI_PER_GIRO = LIMITE_WORKER - ATTESA - MARGINE_CHIUSURA
# ⚠️ E DUE PACCHETTI PER GIRO, non di piu'. Non e' prudenza generica: due
# chiamate da `ATTESA` secondi l'una stanno dentro `SECONDI_PER_GIRO` anche
# nel caso peggiore, tre no. Quel che avanza si manda al giro dopo, e il
# messaggio finale dice quanto avanza. Con le 604 schede del perimetro questo
# tetto non si tocca mai: serve al giorno in cui il catalogo cresce.
MAX_PACCHETTI_PER_GIRO = 2
MAX_SCHEDE_PER_GIRO = MAX_PACCHETTI_PER_GIRO * MAX_PER_PACCHETTO

# ⚠️ IL TETTO DELLA RACCOLTA E' UN ALTRO, ED E' MOLTO PIU' ALTO. Non e'
# distrazione: due nella raccolta AFFAMAVANO tutti gli altri pacchetti per tre
# giorni, e il difetto e' istruttivo.
#
# `cdiscount.pacchetto._order` e' `scade_il asc`, cioe' «prima chi scade
# prima» — che e' giusto. Ma un pacchetto BLOCCATO (uno che resta «in
# lavorazione», o che risponde sempre «non lo so») e' anche quello che scade
# per primo: sta in cima a OGNI giro e occupa uno dei posti finche' non
# scade. Con due bloccati insieme e due posti, per tre giorni il raccoglitore
# non guarda nient'altro, e tutti i pacchetti mandati nel frattempo scadono
# senza essere mai stati letti. Alzare la cadenza del cron non salverebbe:
# la cadenza e' del Compito 12 e da qui non si puo' contarci.
#
# ⚠️ E il tetto piu' alto si PUO' permettere, perche' i costi dei due giri
# sono asimmetrici. Nell'invio, farsi uccidere dal worker costa il NUMERO di
# un pacchetto gia' preso, cioe' un esito irrecuperabile: li' si sta larghi.
# Qui non si scrive niente di irrecuperabile — un giro ucciso lascia i
# pacchetti aperti e il giro dopo li rilegge — quindi il limitatore vero puo'
# essere il TEMPO, che e' anche l'unica cosa che misura il costo reale: un
# pacchetto «in lavorazione» risponde in un istante, e con venti risposte
# istantanee `SECONDI_PER_GIRO` non si tocca nemmeno.
#
# Percio': il tempo ferma il giro, e questo numero e' solo una CINTURA per il
# giorno in cui l'orologio non si muove. Venti sta comodamente dentro il
# tempo con risposte normali, e vuole venti pacchetti bloccati insieme —
# non due — per affamare il ventunesimo.
MAX_PACCHETTI_PER_RACCOLTA = 20

# ⚠️ QUANTI CODICI SI NOMINANO PER ESTESO IN UN MESSAGGIO, e perche' un tetto
# ci vuole. Un pacchetto porta fino a 10.000 schede: se il rapporto non ne
# nomina nessuna — il caso piu' probabile il giorno in cui il nome di una
# chiave non torna — l'elenco delle mancanti sarebbe di 10.000 codici, in un
# campo Text di `centrivo.job.log`, ripetuto a ogni giro del cron ogni mezz'ora
# per tre giorni. E' la stessa lezione di `_dettaglio` nel client: quel che si
# taglia si DICE, e il conto esatto viaggia sempre accanto all'elenco corto.
MAX_CODICI_NEL_MESSAGGIO = 20
# Quanto di un rapporto illeggibile finisce nel messaggio. Serve a riconoscere
# una pagina d'errore di un proxy senza incollarla per intero.
MAX_CORPO_NEL_MESSAGGIO = 300
# ⚠️ QUANTI MOTIVI DIVERSI SI ELENCANO NELLA RIGA FINALE DELL'INVIO, ed e'
# l'ultimo posto in cui il messaggio del registro poteva crescere senza tetto.
# I singoli motivi hanno il loro (il client li taglia per chiave, col tetto
# assoluto), ma il NUMERO dei motivi distinti no: uno scarto di composizione
# NOMINA il prodotto, quindi 10.000 schede scartate una per una fanno 10.000
# motivi diversi in un solo campo Text. La regola e' quella di casa: si
# elencano i piu' frequenti, e quel che si taglia si DICE.
MAX_MOTIVI_NEL_REGISTRO = 20

# ⚠️ LE CHIAVI CHE SANNO DI PAGINAZIONE, e perche' qui c'e' solo un SOSPETTO
# e non un secondo giro di chiamate.
#
# Un pacchetto porta fino a 10.000 schede, ed e' del tutto plausibile che il
# rapporto arrivi a pagine. Un rapporto PAGINATO e uno genuinamente
# INCOMPLETO, da qui, sono indistinguibili: tutti e due nominano meno
# prodotti di quanti ne sono partiti.
#
# ⚠️ E la pagina successiva NON si va a chiedere alla cieca. Sarebbe
# indovinare il nome di un parametro di ricerca — ed e' esattamente il
# rischio che questo file rifiuta per il numero del pacchetto: un parametro
# sbagliato su un server indulgente non risponde 404, risponde
# QUALCOS'ALTRO — un'altra pagina, un altro rapporto — che verrebbe letto
# come se fosse il nostro. Aggiungerlo proprio nel punto in cui il modulo
# puo' mentire sarebbe il peggior posto possibile.
#
# Quel che si fa e' rendere il caso DIAGNOSTICABILE: se il corpo porta una
# chiave che sa di paginazione, la riga rossa lo dice. La paginazione vera si
# scrive dopo la prima lettura reale, sulla forma osservata.
#
# Il confronto e' in minuscolo e senza trattini bassi, cosi' `totalCount` e
# `total_count` sono la stessa cosa: Octopia mescola le due convenzioni a
# seconda dell'endpoint.
CHIAVI_PAGINAZIONE = ("pagination", "total", "totalcount", "page",
                      "pagecount", "nextpage", "hasmore", "links")

# ------------------------------------------------------------------------
# I TRE DEBITI DEI COMPITI PRECEDENTI, e perche' si saldano QUI e non in
# `cdiscount_schede`.
#
# `cdiscount_schede.corpo_scheda` e' la forma di una scheda, e il suo banco
# (tools/test_cdiscount_schede.py, 77 controlli) usa `gtin="1"` nei casi che
# devono PASSARE: mettere li' il controllo di forma sul GTIN farebbe fallire
# un banco che deve restare intatto. E allora i tre controlli stanno insieme
# qui — che e' anche il punto in cui i dati VERI entrano, e l'unico in cui il
# rifiuto puo' nominare il prodotto Odoo da correggere.
#
# ⚠️ Spostarli in `cdiscount_schede` resta la casa giusta a lungo termine:
# e' un seguito che deve aggiornare quel banco, non un lavoro da fare di
# straforo qui.
# ------------------------------------------------------------------------
# 1. IL GTIN. La documentazione dice 8-13 cifre. Un GTIN malformato non e' una
#    scheda rifiutata: e' IL PACCHETTO rifiutato, e lo si scopre tre giorni
#    dopo ripescando l'esito. E non c'e' nessuna ricerca per GTIN prima di
#    mandare che possa avvisarci.
MIN_GTIN = 8
MAX_GTIN = 13
SOLO_CIFRE = re.compile(r"^[0-9]+$")
# 2. I TETTI DI LUNGHEZZA che mancavano. ⚠️ NON sono documentati da Cdiscount:
#    sono scelti qui, e si dichiarano.
#    - il riferimento venditore e' il nostro `default_code`, che in casa e'
#      lungo una decina di caratteri: 50 e' abbondante, e cio' che lo supera
#      non e' uno SKU lungo, e' un'altra colonna finita nella sua;
#    - la marca: i marchi veri stanno in venti caratteri («Ideal Standard» ne
#      fa 14). Una marca di 300 caratteri e' una descrizione, e oggi
#      partirebbe intatta verso un catalogo pubblico;
#    - la categoria e' un codice di sei caratteri (vedi `_categoria`): 40 e'
#      dieci volte quel che serve, e serve solo a fermare l'incollatura.
#    Come per il titolo e la descrizione, **non si taglia: ci si ferma**. Un
#    testo mozzo viene accettato e finisce in vetrina cosi'.
MAX_RIFERIMENTO = 50
MAX_MARCA = 60
MAX_CATEGORIA = 40
# 3. LE IMMAGINI arrivano gia' compattate, e non c'e' niente da compattare:
#    vedi `_immagini`.

# ⚠️ LA FORMA DI UN CODICE DI CATEGORIA. Non e' un'espressione decorativa:
# l'errore vero non e' un codice storto, e' il NOME della categoria copiato
# al posto del codice («Cabines de douche»). Vedi `_categoria`.
FORMA_CATEGORIA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# ⚠️ DOVE STA IL NUMERO DEL PACCHETTO, e perche' i nomi sono quattro piu' uno.
# La documentazione dice `packageId`; le API di Octopia mescolano `camelCase`
# e `snake_case` a seconda dell'endpoint, e questo nome non e' mai stato visto
# sul vero. Provarne uno solo vuol dire, se sbagliamo, un pacchetto SENZA
# NUMERO a ogni invio — cioe' il guasto peggiore del modulo, ripetuto.
# `id` sta in un secondo elenco e si guarda SOLO se nessuno dei quattro c'e':
# e' il nome piu' generico e potrebbe appartenere a tutt'altro.
CHIAVI_NUMERO = ("packageId", "packageID", "package_id", "packageid")
CHIAVI_NUMERO_RIPIEGO = ("id",)

# ⚠️ LA FORMA DI UN NUMERO DI PACCHETTO, E PERCHE' SOLO SUL RIPIEGO.
# Misurato in revisione: un corpo JSON scalare `"Accepted"` — forma
# ORDINARIA per un 202 di un'API asincrona, e lo stato del successo e' fra le
# cose che di Cdiscount non sappiamo — diventava il numero di pacchetto
# «Accepted». Si scriveva `cdiscount.pacchetto`, si attaccavano fino a 10.000
# righe, e LA NOTIFICA USCIVA VERDE: fra tre giorni il raccoglitore avrebbe
# chiesto l'esito del pacchetto «Accepted», non l'avrebbe trovato, e l'esito
# vero sarebbe scaduto senza che nessuno sapesse perche'. E' esattamente cio'
# che la testa di questo file vieta — «un nome che non torna produce numero
# non conosciuto, mai un falso successo».
#
# La regola: almeno UNA CIFRA e nessuno spazio. `AB-9` passa (un
# identificativo alfanumerico e' plausibile), `Accepted`, `queued`, `OK` e
# `4 2` no.
#
# ⚠️ VALE SU TUTTI I PERCORSI, comprese le quattro chiavi che dicono
# `package…`. Al primo giro l'avevo messa solo sul ripiego, ragionando che
# «la' e' il server a dichiarare che quel valore E' il numero»: era un
# ragionamento sbagliato, e lo nega questo stesso file tre righe piu' su —
# **quei quattro nomi non sono mai stati visti sul vero**, sono quattro
# tentativi proprio perche' non si sa quale sia giusto. Se il NOME e' una
# supposizione, «il server dichiara» e' una supposizione anche lui: si
# indovina il nome e poi ci si fida ciecamente del valore che ci sta sotto.
# Misurato: `{"packageId": "Accepted"}` dava il pacchetto numero «Accepted».
#
# ⚠️ E i costi sono asimmetrici. Rifiutare un numero buono costa una fermata
# **che stampa il corpo della risposta** e un secondo clic; accettare
# «Accepted» costa un pacchetto perso in silenzio, e con lui fino a 10.000
# schede. Il rischio di stringere e' ipotetico (un identificativo legittimo
# senza nemmeno una cifra); quello di non stringere era misurato.
#
# ⚠️ E se la chiave esplicita c'e' ma la sua forma non torna, NON si ripiega
# su `id` ne' sul corpo nudo: la chiave che nomina il pacchetto porta
# qualcosa che non e' un numero di pacchetto, cioe' la risposta non l'abbiamo
# capita. Cercare un secondo parere sarebbe indovinare sopra una
# contraddizione.
FORMA_NUMERO = re.compile(r"^[^\s]*[0-9][^\s]*$")


def _numero_testo(grezzo):
    """Il numero di pacchetto come testo, e senza decimali.

    ⚠️ `str(1234.0)` da' «1234.0», che non corrisponde a nessun pacchetto e
    non tornera' mai piu': il JSON non garantisce il tipo, e un numero
    arrivato come float va normalizzato qui, in un punto solo. E' la stessa
    lezione di `kaufland._testo_id`.

    ⚠️ Un booleano NON e' un numero: `_testo` lo tradurrebbe in «True», che
    e' un numero di pacchetto inventato — e un numero inventato e' peggio di
    nessun numero, perche' non si distingue da uno buono.
    """
    if grezzo is None or isinstance(grezzo, bool):
        return ""
    if isinstance(grezzo, float):
        return str(int(grezzo)) if grezzo.is_integer() else str(grezzo)
    return _testo(grezzo)


def _numero_fra(corpo, chiavi):
    """Il numero letto da una famiglia di nomi, o "" se non e' univoco.

    ⚠️ Stessa disciplina di `cdiscount_rapporto._codice_voce`, e per una
    ragione piu' grave: se due nomi portano DUE numeri diversi non sappiamo
    quale sia il pacchetto, e sceglierne uno significherebbe sorvegliare per
    tre giorni l'esito di un pacchetto che non e' il nostro — mentre quello
    vero scade. Meglio «non lo conosco», che si ferma e grida.
    """
    trovati = []
    for chiave in chiavi:
        grezzo = corpo.get(chiave)
        if not _e_scalare(grezzo):
            # Un valore composto male CON CONTENUTO rende ambigua la
            # risposta; vuoto («la chiave non c'e'») no.
            if grezzo:
                return ""
            continue
        numero = _numero_testo(grezzo)
        if numero and numero not in trovati:
            trovati.append(numero)
    return trovati[0] if len(trovati) == 1 else ""


def _elenco_corto(codici):
    """Un elenco di codici da mettere in un messaggio, col tetto dichiarato.

    ⚠️ Il TETTO E' LA RAGIONE per cui questa funzione esiste, e il conto
    esatto non sta qui: sta accanto, nel messaggio che la chiama («NON nomina
    %(quante)s schede: %(elenco)s»). Cosi' l'elenco puo' accorciarsi senza che
    il numero menta mai — che e' l'errore opposto, e piu' grave, di un elenco
    lungo. Un pacchetto porta fino a 10.000 schede, e il caso peggiore — un
    rapporto che non ne nomina nemmeno una — e' anche il piu' probabile il
    giorno in cui il nome di una chiave non torna.
    """
    codici = list(codici)
    if len(codici) <= MAX_CODICI_NEL_MESSAGGIO:
        return ", ".join(codici)
    return _("%(primi)s … e altri %(quanti)s") % {
        "primi": ", ".join(codici[:MAX_CODICI_NEL_MESSAGGIO]),
        "quanti": len(codici) - MAX_CODICI_NEL_MESSAGGIO}


def _chiavi_di_paginazione(risposta):
    """I nomi, come li scrive Cdiscount, delle chiavi che sanno di pagine.

    ⚠️ Guarda il corpo GREZZO e anche quello scartato dell'involucro `data`:
    non sappiamo quale delle due forme abbia il rapporto, e una paginazione
    puo' stare tanto accanto ai dati quanto dentro. Vedi
    `CHIAVI_PAGINAZIONE` per il perche' qui ci si ferma al sospetto.
    """
    trovate = []
    for corpo in (risposta.corpo, risposta.dati):
        if not isinstance(corpo, dict):
            continue
        for chiave in corpo:
            nome = _testo(chiave)
            if nome.lower().replace("_", "") in CHIAVI_PAGINAZIONE:
                if nome not in trovate:
                    trovate.append(nome)
    return trovate


@register_connector("cdiscount", "Cdiscount (Octopia)")
class CdiscountConnector(MarketplaceConnector):

    default_base_url = CDISCOUNT_URL

    # ------------------------------------------------------------------
    # Il gettone e il client
    # ------------------------------------------------------------------
    def _gettone(self):
        """Un token valido, rinnovato se serve. Solleva se non arriva.

        ⚠️ E' una FUNZIONE e non una stringa perche' il client la chiama a
        ogni richiesta: fra un giro e l'altro il token puo' essere scaduto, e
        la logica della scadenza sta tutta in `cdiscount_token` — che non
        importa Odoo apposta, per potersi provare senza.

        ⚠️ Le credenziali si leggono in `sudo()`: i campi hanno
        `groups="base.group_system"`, quindi a un utente normale
        risulterebbero VUOTE e l'errore direbbe «mancano le credenziali»
        invece di «non hai il permesso di vederle» — mandando a cercare il
        guasto nel posto sbagliato.

        ⚠️ La scrittura del token in banca dati passa da `_al_riparo` e il
        suo ritorno SI LEGGE: se non si e' potuto memorizzare, il giro va
        avanti lo stesso (il token buono ce l'abbiamo in mano), ma lo si
        dice nel registro — altrimenti ogni chiamata ne chiederebbe uno
        nuovo per sempre, senza che nessuno sappia perche'.
        """
        canale = self.channel.sudo()
        adesso = time.time()
        gettone = (canale.cdiscount_gettone or "").strip()
        if gettone and not serve_rinnovo(canale.cdiscount_gettone_scade,
                                         adesso):
            return gettone

        chiave = (canale.cdiscount_client_id or "").strip()
        segreto = (canale.cdiscount_client_secret or "").strip()
        if not chiave or not segreto:
            raise UserError(_(
                "Sul canale «%s» mancano il Client ID o il Segreto "
                "Cdiscount: senza, non si puo' nemmeno chiedere il gettone.")
                % self.channel.display_name)

        corpo = urlencode({"grant_type": "client_credentials",
                           "client_id": chiave,
                           "client_secret": segreto}).encode("utf-8")
        stato, testo, _teste = TrasportoRequests().chiama(
            "POST", TOKEN_URL,
            {"Content-Type": "application/x-www-form-urlencoded",
             "Accept": "application/json"},
            corpo)
        dati = None
        if testo:
            try:
                dati = json.loads(testo)
            except ValueError:
                dati = None
        nuovo = ""
        if isinstance(dati, dict):
            nuovo = _testo(dati.get("access_token"))
        if not (200 <= int(stato or 0) < 300) or not nuovo:
            # ⚠️ Non si dice «credenziali sbagliate»: uno stato 0 e' la rete
            # caduta, e mandare a rigenerare un segreto che va benissimo
            # costa un pomeriggio. Si riporta quel che e' successo.
            raise UserError(_(
                "Il gettone Cdiscount non e' arrivato (stato %(stato)s): "
                "%(testo)s\n\nFinche' non arriva non parte nessuna scheda. "
                "Uno stato 0 vuol dire che la risposta si e' persa per "
                "strada (rete o tempo scaduto), non che le credenziali "
                "siano sbagliate.")
                % {"stato": stato,
                   "testo": (testo or _("nessun dettaglio"))[:300]})

        durata = dati.get("expires_in") if isinstance(dati, dict) else 0
        try:
            durata = int(durata or 0)
        except (TypeError, ValueError):
            # ⚠️ Una durata illeggibile vale ZERO, cioe' «gia' scaduto»:
            # `cdiscount_token.scadenza` la tratta cosi' apposta. Il prossimo
            # giro ne chiedera' un altro — che costa una chiamata, mentre
            # indovinare una durata costa un'ora di 401 che sembrano
            # credenziali sbagliate.
            durata = 0
        scade = scadenza(adesso, durata)
        if not self._al_riparo(canale.write, {"cdiscount_gettone": nuovo,
                                              "cdiscount_gettone_scade": scade}):
            _logger.warning(
                "Cdiscount sul canale %s: il gettone e' arrivato ma non si e' "
                "potuto memorizzare. Il giro prosegue, ma ogni chiamata ne "
                "chiedera' uno nuovo finche' la scrittura non riesce.",
                self.channel.display_name)
        return nuovo

    def _client(self):
        """Il client di QUESTO canale, col venditore e il canale di vendita.

        ⚠️ Le tre intestazioni obbligatorie sono `Authorization`, `SellerId` e
        `SalesChannelId`: senza la terza alcune chiamate rispondono 400
        dicendolo per nome. Il `SellerId` si legge in `sudo()` per la stessa
        ragione delle credenziali.
        """
        canale = self.channel.sudo()
        venditore = (canale.cdiscount_seller_id or "").strip()
        if not venditore:
            raise UserError(_(
                "Sul canale «%s» manca il SellerId Cdiscount: viaggia in "
                "un'intestazione di ogni chiamata, e senza l'API non sa per "
                "conto di chi si parla.") % self.channel.display_name)
        return CdiscountClient(
            venditore, self._canale_vendita(), TrasportoRequests(),
            self._gettone, base_url=canale.base_url or CDISCOUNT_URL)

    # ------------------------------------------------------------------
    # LE DATE CHE UNA PERSONA LEGGE
    #
    # ⚠️ Odoo tiene i Datetime in UTC e li mostra ovunque nel fuso di chi
    # guarda. Una data infilata a mano dentro un testo NON passa da quella
    # conversione: d'estate, in Italia, scrive DUE ORE PRIMA di quel che la
    # stessa data mostra due centimetri piu' in la', nella colonna «L'esito
    # scade il» della stessa schermata. Chi confronta le due conclude che una
    # delle due e' sbagliata — e non sa quale.
    #
    # ⚠️ E su una scadenza il danno non e' cosmetico. Tutti i messaggi di
    # questo file esistono per far guardare un pacchetto PRIMA che il suo
    # esito diventi irrecuperabile: un'ora sbagliata in meno accorcia la
    # finestra che si sta annunciando, e su un termine di attivita' — che e'
    # una DATA, senza ore — due ore in meno possono farla cadere il GIORNO
    # PRIMA.
    #
    # E' la stessa cura che `cdiscount.scheda.action_cdiscount_ripesca`
    # applica alla nota del ripescaggio, per lo stesso identico motivo.
    # ------------------------------------------------------------------
    def _ora_locale(self, quando):
        """Un istante UTC scritto nel fuso di chi legge, col nome del fuso.

        ⚠️ `%Z` in coda non e' decorazione: un'ora senza il nome del fuso non
        e' verificabile da chi la rilegge sei mesi dopo, e il registro delle
        operazioni si rilegge proprio cosi'. Se l'utente non ha un fuso
        impostato, `context_timestamp` rende UTC e `%Z` scrive «UTC»: dice il
        vero in tutti e due i casi.

        ⚠️ Vuoto rende una frase, non una stringa vuota: un «L'esito scade il
        .» a fine riga si legge come un guasto del programma.
        """
        quando = fields.Datetime.to_datetime(quando)
        if not quando:
            return _("(non letta)")
        return fields.Datetime.context_timestamp(
            self.channel, quando).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _giorno_locale(self, quando):
        """Il GIORNO di un istante UTC, nel fuso di chi legge.

        ⚠️ Serve ai termini delle attivita', che in Odoo sono date senza ora.
        `scade.date()` su un UTC nudo taglia il giorno sbagliato ogni volta
        che la conversione attraversa la mezzanotte: un pacchetto che scade
        alle 00:30 di martedi' (ora italiana) e' registrato alle 22:30 di
        lunedi' UTC, e il termine dell'attivita' cadrebbe LUNEDI' — un giorno
        prima di quello annunciato nel testo dell'attivita' stessa.
        """
        quando = fields.Datetime.to_datetime(quando)
        if not quando:
            return None
        return fields.Datetime.context_timestamp(self.channel, quando).date()

    def _registra(self, operazione, esito, messaggio, payload=None,
                  external_id=None):
        """Una riga nel registro delle operazioni.

        ⚠️ Il messaggio arriva gia' col dettaglio dentro (vedi
        `cdiscount_client.RispostaCdiscount.messaggio`, che tronca CHIAVE PER
        CHIAVE): non accorciarlo qui, e' l'unica cosa che dice QUALE campo
        Cdiscount ha rifiutato e perche'.
        """
        return self.env["centrivo.job.log"].sudo().create({
            "channel_id": self.channel.id,
            "operation": operazione,
            "external_id": external_id,
            "result": esito,
            "message": messaggio,
            "payload": (payload or "")[:MAX_PAYLOAD] or False,
            "company_id": self.channel.company_id.id,
        })

    # ------------------------------------------------------------------
    # Le guardie
    # ------------------------------------------------------------------
    def _canale_vendita(self):
        """Il canale di vendita Octopia, o ci si ferma.

        E' una Selection con una voce sola (`CDISFR`), ma vuota si legge
        `False`: senza questa guardia partirebbe l'intestazione
        `SalesChannelId: False`.
        """
        canale = (self.channel.sudo().cdiscount_canale_vendita or "").strip()
        if not canale:
            raise UserError(_(
                "Sul canale «%s» non e' indicato il canale di vendita "
                "Cdiscount. Va scelto sulla scheda del canale: viaggia in "
                "un'intestazione di ogni chiamata.")
                % self.channel.display_name)
        return canale

    def _categoria(self):
        """La categoria in cui nascono le schede — LA GUARDIA SERIA.

        ⚠️ **Questa e' la protezione contro una categoria cambiata per
        sbaglio, e non ce n'e' un'altra.** Il campo `cdiscount_categoria` non
        ha `groups=` (scelta del Compito 7: dev'essere scrivibile dalla
        schermata, come il gruppo di spedizione di Kaufland), e
        `centrivo.channel` e' in scrittura a `base.group_user`: qualunque
        utente interno puo' cambiarlo. Se il valore e' sbagliato, **tutte** le
        schede del prossimo pacchetto nascono nel ramo sbagliato del catalogo
        pubblico — fino a 10.000 — e lo si scopre tre giorni dopo, o mai.

        Cosa questa guardia PUO' fare:

        - fermarsi se la categoria manca;
        - fermarsi se non ha la forma di un CODICE. ⚠️ E' il controllo che
          conta davvero: l'errore vero non e' un codice storto, e' il NOME
          della categoria incollato al posto del codice — «Cabines de
          douche», con gli spazi e gli accenti. Un nome passerebbe qualunque
          controllo di sola presenza e farebbe rifiutare il pacchetto intero;
        - fermarsi se e' assurdamente lunga (vedi `MAX_CATEGORIA`);
        - **dirla ad alta voce**: la categoria finisce nel registro e nella
          notifica di ogni invio, cosi' un cambiamento si vede la prima volta
          che si manda e non alla prima raccolta.

        ⚠️ Cosa questa guardia NON puo' fare, e va detto chiaro: **verificare
        che sia di LIVELLO 3**. Una categoria di primo livello ha la stessa
        forma — sei caratteri — e da qui e' indistinguibile (lo dice gia'
        `cdiscount_schede.corpo_scheda`). L'unico modo di saperlo e' leggerla
        dal portale, ramo per ramo fino alla foglia. Chiedere a Cdiscount
        (`GET /categories`) prima di ogni invio sarebbe possibile, ma il nome
        di quell'endpoint non e' mai stato verificato sul vero: un 404 dovuto
        al nostro percorso sbagliato bloccherebbe OGNI invio per sempre, ed e'
        un guasto peggiore di quello che eviterebbe. Resta un seguito da fare
        DOPO la prima lettura vera.
        """
        grezza = self.channel.sudo().cdiscount_categoria
        categoria = _testo(grezza)
        if not categoria:
            raise UserError(_(
                "Sul canale «%s» manca la categoria Cdiscount. Va letta dal "
                "portale, ramo per ramo fino alla foglia, e dev'essere di "
                "LIVELLO 3: le schede senza categoria vengono rifiutate, e "
                "una categoria del livello sbagliato fa rifiutare il "
                "pacchetto intero tre giorni dopo.")
                % self.channel.display_name)
        if len(categoria) > MAX_CATEGORIA:
            raise UserError(_(
                "La categoria del canale «%(canale)s» e' lunga %(quanti)d "
                "caratteri e un codice di categoria Cdiscount ne fa sei: "
                "quasi certamente e' stato incollato qualcosa d'altro. Non "
                "si accorcia da qui — con la categoria sbagliata nascerebbe "
                "un catalogo intero nel ramo sbagliato.")
                % {"canale": self.channel.display_name,
                   "quanti": len(categoria)})
        if not FORMA_CATEGORIA.match(categoria):
            raise UserError(_(
                "La categoria del canale «%(canale)s» e' «%(categoria)s», e "
                "non ha la forma di un CODICE (lettere e cifre, "
                "eventualmente con trattini o trattini bassi). Quasi sempre "
                "vuol dire che e' stato copiato il NOME della categoria "
                "invece del suo codice: il nome fa rifiutare il pacchetto "
                "intero, e lo si scopre tre giorni dopo.")
                % {"canale": self.channel.display_name,
                   "categoria": categoria})
        return categoria

    # ------------------------------------------------------------------
    # Da una riga `cdiscount.scheda` al corpo di una scheda
    # ------------------------------------------------------------------
    @staticmethod
    def _gtin(prodotto, chi):
        """Il GTIN del prodotto, controllato nella forma. ⚠️ DEBITO 1.

        La documentazione dice **da 8 a 13 cifre**. Un GTIN malformato non
        costa una scheda: costa il PACCHETTO, e siccome l'esito e' asincrono
        lo si scopre tre giorni dopo. E non esiste nessuna ricerca per GTIN
        prima di mandare che possa avvisarci — e' precisamente cio' che
        Cdiscount non offre.

        ⚠️ Il controllo e' sulla FORMA e non sulla cifra di controllo: GTIN-8,
        12, 13 e 14 hanno regole diverse, e un controllo sbagliato
        bloccherebbe codici buoni. Un codice della forma giusta ma con la
        cifra di controllo sbagliata lo rifiuta Cdiscount, dicendolo.
        """
        codice = _testo(prodotto.barcode if prodotto else "")
        if not codice:
            raise ValueError(
                "%s: il prodotto Odoo non ha codice a barre, e il GTIN si "
                "prende da li'. Senza GTIN la scheda non si compone: non "
                "esiste nessun modo di sapere se esista gia' su Cdiscount."
                % chi)
        if not SOLO_CIFRE.match(codice) or not (
                MIN_GTIN <= len(codice) <= MAX_GTIN):
            raise ValueError(
                "%s: il codice a barre e' «%s», e un GTIN e' fatto di sole "
                "cifre, da %d a %d. Cosi' com'e' farebbe rifiutare il "
                "PACCHETTO INTERO, e il rifiuto si legge tre giorni dopo."
                % (chi, codice, MIN_GTIN, MAX_GTIN))
        return codice

    def _marca(self, prodotto, chi):
        """La marca del prodotto. ⚠️ DEBITO 2 (il tetto di lunghezza).

        Viene dal modulo OCA `product_brand`, letto a runtime come fa gia'
        BricoBravo (`_brand_value`): se il modulo non c'e', il campo non c'e'
        e la scheda si ferma dicendo dove andare a mettere la marca — non
        parte senza.
        """
        if not prodotto or "product_brand_id" not in prodotto._fields:
            raise ValueError(
                "%s: in Odoo non c'e' un campo marca sul prodotto (serve il "
                "modulo OCA `product_brand`). Cdiscount confronta la marca "
                "col proprio elenco e senza non accetta la scheda." % chi)
        marca = prodotto.product_brand_id
        nome = _testo(marca.name if marca else "")
        if not nome:
            raise ValueError(
                "%s: il prodotto «%s» non ha una marca. Cdiscount la "
                "confronta col proprio elenco, e una scheda senza marca non "
                "passa." % (chi, prodotto.display_name))
        return _entro_il_limite(nome, MAX_MARCA, chi, "la marca")

    @staticmethod
    def _immagini(riga):
        """Gli indirizzi delle immagini della riga. ⚠️ DEBITO 3.

        ⚠️ **La lista arriva gia' compattata, e non c'e' niente da
        compattare.** Il pericolo dichiarato nel debito e' costruire la lista
        da CAMPI ODOO A POSIZIONE FISSA — `immagine_1 … immagine_5` — dove
        una casella vuota diventa un `False` in mezzo e fa cadere una scheda
        perfettamente buona. Qui quei campi non esistono: gli indirizzi
        stanno in UN SOLO campo di testo (`cdiscount.scheda.immagini`), sono
        esattamente la cella scritta nel file dei contenuti, e
        `elenco_immagini()` la spezza sulla virgola senza aggiungere ne'
        togliere posizioni. Con una sola immagine si ottiene una lista di UNO.
        Il banco lo verifica.

        ⚠️ E cio' che resta vuoto NON si toglie qui, ed e' voluto: una voce
        vuota in mezzo vuol dire che qualcuno ha lasciato un buco nella cella,
        e togliere un buco fa scivolare avanti tutte le immagini seguenti —
        il prodotto va in vetrina con la copertina sbagliata, senza che resti
        traccia da nessuna parte. La rifiuta `_immagini_valide` nominando la
        posizione, ed e' la stessa scelta gia' presa e motivata in tre punti
        (`cdiscount_schede._immagini_valide`, `cdiscount_contenuti._immagini`,
        `cdiscount.scheda.elenco_immagini`). Compattare qui le
        contraddirebbe tutte e tre in silenzio.
        """
        return riga.elenco_immagini()

    def _corpo(self, riga, categoria):
        """Il corpo di UNA scheda, o un ValueError che nomina il prodotto.

        ⚠️ Il tipo del rifiuto e' un contratto: **solo `ValueError`**, come in
        `cdiscount_schede` e in `cdiscount_contenuti`. Chi compone il giro lo
        cattura riga per riga per scartare quella riga; un tipo diverso
        farebbe morire l'invio intero invece di scartare una scheda.
        """
        codice = _testo(riga.codice)
        chi = _chi(codice)
        _entro_il_limite(codice, MAX_RIFERIMENTO, chi,
                         "il riferimento venditore")
        prodotto = riga.product_id
        if not prodotto:
            raise ValueError(
                "%s: nessun prodotto Odoo collegato. Il GTIN e la marca si "
                "prendono dal prodotto: senza, la scheda non si compone." % chi)
        return corpo_scheda(
            codice=codice,
            gtin=self._gtin(prodotto, chi),
            titolo=riga.titolo,
            descrizione=riga.descrizione,
            immagini=self._immagini(riga),
            categoria=categoria,
            marca=self._marca(prodotto, chi),
        )

    # ------------------------------------------------------------------
    # Chi parte
    # ------------------------------------------------------------------
    def _candidate(self):
        """Il dominio delle schede che possono partire.

        Tre condizioni, e la terza e' l'unica interessante:

        1. sono di QUESTO canale;
        2. hanno tutti e tre i contenuti (`CON_CONTENUTI`: titolo,
           descrizione e immagini — il dominio vive nel modello e lo usano in
           tre);
        3. **o non sono mai partite, o Cdiscount ha detto di NO.**

        ⚠️ La terza e' un'asimmetria voluta, e sta tutta qui: `rifiutato`
        significa che Cdiscount ci ha detto che la scheda **non e' nata**, e
        allora rimandarla non puo' duplicare niente — e' anzi l'unico modo
        perche' una traduzione corretta arrivi a destinazione. `sconosciuto`
        **con un pacchetto** e' l'opposto: e' un pacchetto scaduto senza
        esito, la scheda potrebbe esistere gia' la' fuori, e rimandarla
        creerebbe un doppione su un catalogo pubblico. Quelle si guardano a
        mano.

        ⚠️ E `riuscito` e `in_attesa` non compaiono affatto: la prima esiste
        gia', la seconda e' in volo.

        ⚠️ **Ma una `rifiutato` aspetta che il suo pacchetto sia CHIUSO.** Il
        verdetto di rifiuto puo' arrivare da un rapporto PARZIALE: in quel
        caso il pacchetto resta `aperto` (regola 2 del raccoglitore) perche'
        le altre sue schede non hanno ancora un verdetto, e il raccoglitore ci
        ripassa ogni mezz'ora. Se la riga rifiutata ripartisse subito, si
        staccherebbe da quel pacchetto per prendere il numero nuovo — e al
        giro dopo il rapporto del pacchetto vecchio nominerebbe un codice che
        li' dentro non c'e' piu': una VOCE ESTRANEA. Misurato: da li' scattava
        l'attivita' «il rapporto non nomina nessuna scheda, e' il sospetto di
        una CHIAVE SBAGLIATA su TUTTI i pacchetti» — che e' falsa, e che e' la
        diagnosi piu' cara del modulo — e la riga di registro restava rossa a
        ogni giro finche' il pacchetto non scadeva.

        ⚠️ Il costo e' dichiarato: una scheda rifiutata di cui si corregge il
        dato aspetta che il suo pacchetto si chiuda, e nel caso peggiore sono
        i tre giorni della scadenza (che chiude il pacchetto a `scaduto`,
        cioe' non piu' `aperto`, e la rimette in gioco da sola). In cambio non
        si crea mai un disallineamento che nessuno sa leggere. La condizione e'
        «il pacchetto non c'e' piu' OPPURE non e' piu' aperto», non «e'
        chiuso»: un pacchetto cancellato lascia `pacchetto_id` vuoto
        (`ondelete="set null"`), e una riga cosi' non deve restare ferma per
        sempre.
        """
        return [("channel_id", "=", self.channel.id)] + list(CON_CONTENUTI) + [
            "|",
            "&", ("stato", "=", SCONOSCIUTO_SCHEDA),
            ("pacchetto_id", "=", False),
            "&", ("stato", "=", RIFIUTATO),
            "|", ("pacchetto_id", "=", False),
            ("pacchetto_id.stato", "!=", APERTO),
        ]

    # ------------------------------------------------------------------
    # Il numero, e cosa si fa quando non c'e'
    # ------------------------------------------------------------------
    @staticmethod
    def _numero_pacchetto(risposta):
        """Il numero del pacchetto dentro una risposta ANDATA BENE, o "".

        Octopia incarta gli elenchi in `data` e restituisce nudi gli oggetti
        singoli: `.dati` conosce gia' tutte e due le forme. Qui si guarda
        anche il caso della risposta che porta **solo il numero** — un corpo
        JSON scalare, che `.dati` non sa rendere perche' non e' ne' un
        dizionario ne' un elenco.
        """
        dati = risposta.dati
        if isinstance(dati, dict):
            numero = _numero_fra(dati, CHIAVI_NUMERO)
            if numero:
                # ⚠️ La forma si pretende ANCHE qui, e se non torna ci si
                # ferma senza cercare un secondo parere: vedi `FORMA_NUMERO`.
                return numero if FORMA_NUMERO.match(numero) else ""
            numero = _numero_fra(dati, CHIAVI_NUMERO_RIPIEGO)
            if numero and FORMA_NUMERO.match(numero):
                return numero
        for grezzo in (dati, risposta.corpo):
            if _e_scalare(grezzo):
                numero = _numero_testo(grezzo)
                if numero and FORMA_NUMERO.match(numero):
                    return numero
        return ""

    def _crea_pacchetto(self, numero, adesso, scade):
        """Scrive la riga del pacchetto e la manda SUBITO al database.

        ⚠️ Il `flush_all()` e' una CINTURA, e va detto com'e': il
        `cr.savepoint()` di Odoo scarica gia' da solo prima di rilasciare il
        savepoint, quindi l'INSERT partirebbe comunque dentro la protezione
        di `_al_riparo`. Questa riga rende esplicito cio' su cui il metodo
        conta — che il numero sia GIA' in banca dati quando torna — invece di
        appoggiarsi a un dettaglio di implementazione dell'ORM. Toglierla,
        oggi, non cambia il comportamento: il banco lo dichiara fra le
        mutazioni sopravvissute, e la ragione e' questa, non una sua
        debolezza.
        """
        self.env["cdiscount.pacchetto"].sudo().create({
            "channel_id": self.channel.id,
            "numero": numero,
            # `tipo` ha default «schede» sul modello, ed e' l'unico valore che
            # esiste oggi: le offerte sono la Consegna 2 e avranno il loro.
            "nato_il": adesso,
            "scade_il": scade,
        })
        self.env.flush_all()

    def _pacchetto_scritto(self, numero):
        """La riga del pacchetto appena scritta, riletta DENTRO un savepoint.

        ⚠️ Si rilegge invece di tenersi il record reso dal `create`: quel
        `create` e' avvenuto dentro `_al_riparo`, che inghiotte tutto e
        restituisce solo True o False. Rileggere e' l'unico modo di dire «c'e'
        davvero» invece di «dovrebbe esserci».

        ⚠️ E LA RILETTURA STA DENTRO UN SAVEPOINT, benche' sia «solo una
        SELECT su una tabella appena scritta». Era l'istruzione di database
        piu' esposta DOPO la scrittura del numero, e il varco piu' largo al
        rollback silenzioso di tutto il modulo: se quella SELECT
        fallisce, PostgreSQL abortisce la transazione, da li' `_al_riparo`
        non riesce nemmeno a emettere il `SAVEPOINT`, la chiusura del giro
        cade nel suo `except`, e il commit finale diventa un ROLLBACK che si
        porta via il numero scritto un istante prima. Probabilita' bassa,
        danno massimo, costo della difesa una funzione annidata.

        ⚠️ **NON e' pero' rimasto zero**, e il confine va detto: dopo il
        numero restano le letture di `self.channel.display_name` nei quattro
        `_logger.error`/`_logger.exception` e di `r.codice` nell'elenco delle
        schede. Girano SUBITO DOPO un savepoint rotolato indietro, cioe' con
        la cache dell'ORM appena invalidata: sono SELECT vere, della stessa
        classe di questa. Restano scoperte per scelta — proteggerle
        significherebbe avvolgere ogni riga di log, e il messaggio che si
        perderebbe e' proprio quello che grida il numero — ma chi passa di qui
        sappia che il confine e' li'. Il banco non puo' vederlo: nel finto
        `display_name` e' un attributo Python normale.
        """
        trovato = {}

        def _leggi():
            trovato["riga"] = self.env["cdiscount.pacchetto"].sudo().search(
                [("channel_id", "=", self.channel.id),
                 ("numero", "=", numero)], limit=1)

        self._al_riparo(_leggi)
        return trovato.get("riga") or None

    def _in_volo_senza_esito(self, righe, motivo):
        """Le righe di un pacchetto di cui non sappiamo l'esito.

        ⚠️ Passano a `in_attesa` — non restano `sconosciuto` — e non e' una
        sfumatura: `sconosciuto` senza pacchetto e' precisamente lo stato di
        chi PUO' RIPARTIRE (vedi `_candidate`), e al giro dopo il pacchetto
        verrebbe rimandato alla cieca. Sono partite davvero: `in_attesa` lo
        dice, le toglie dai candidati, e il motivo scritto sopra dice che
        nessun raccoglitore le risolvera' da solo.

        ⚠️⚠️ **E IL PACCHETTO SI STACCA, ed e' la seconda meta' della stessa
        difesa.** Fra i candidati c'e' anche una `rifiutato` con addosso il
        suo VECCHIO pacchetto, ormai chiuso (e' la regola di `_candidate`:
        una rifiutata riparte quando il suo pacchetto non e' piu' aperto).
        Scrivendo solo lo stato, quella riga diventava `in_attesa` **con un
        pacchetto chiuso attaccato**: fuori dai candidati, fuori da
        `_dominio_aperti` (il pacchetto non e' `aperto`), fuori da `ORFANE`
        (che vuole il pacchetto assente) — e in mezzo alle «In attesa
        dell'esito» vere, dove nessun filtro la distingueva. Il gemello esatto
        del vicolo cieco che le arenate hanno chiuso, spostato di uno stato. E
        non e' un caso di laboratorio: dopo ogni raccolta normale TUTTE le
        rifiutate hanno un pacchetto `raccolto`, quindi basta un intoppo di
        rete durante il rinvio.
        
        ⚠️ Staccarlo e' anche **la cosa vera**: quella riga non e' in nessun
        pacchetto che Odoo conosca. O l'invio si e' fermato prima che un
        pacchetto esistesse, o il numero non siamo riusciti a scriverlo, o non
        siamo riusciti ad attaccarci le righe. Il vecchio pacchetto non
        c'entra niente con il perche' la riga e' ferma, e lasciarlo li' era il
        dato falso da cui il filtro e il messaggio prendevano la loro bugia.
        Cosi' la riga ricade in `ORFANE`, che e' il criterio giusto per lei.

        ⚠️ **Il numero vecchio non si perde**: si scrive nel motivo, come fa
        il ripescaggio con le arenate. Dopo il distacco e' l'unico appiglio
        per chi dovesse cercare a mano cosa c'era in quel pacchetto.

        ⚠️ La LETTURA dei numeri vecchi sta dentro `_al_riparo`, e non e'
        pignoleria: questo metodo lo si chiama quando qualcosa e' gia' andato
        storto, e su una transazione abortita una SELECT non protetta si
        porterebbe via l'unica scrittura che deve ancora riuscire. Se non si
        riesce a leggerli, si scrive lo stesso — senza la nota sul pacchetto
        vecchio, che e' un di piu'.
        """
        Scheda = self.env["cdiscount.scheda"].sudo()
        # Le righe raggruppate per numero del pacchetto vecchio: i numeri
        # distinti sono pochi (nessuno, o quello del rifiuto), e un `write`
        # per riga su 10.000 righe sarebbero 10.000 UPDATE.
        gruppi = {}

        def _leggi():
            for riga in righe:
                gruppi.setdefault(_testo(riga.pacchetto_id.numero),
                                  []).append(riga.id)

        if not self._al_riparo(_leggi):
            gruppi = {"": [riga.id for riga in righe]}

        fallita = 0
        stretta = 0
        for vecchio, ids in gruppi.items():
            testo = motivo
            if vecchio:
                testo += _(
                    " ⚠️ Questa scheda risultava ancora nel pacchetto "
                    "%s, che era gia' chiuso: il collegamento e' stato tolto "
                    "perche' non era piu' vero, e il numero resta scritto qui "
                    "per chi dovesse cercarlo.") % vecchio
            gruppo = Scheda.browse(ids)
            if self._al_riparo(gruppo.write, {
                    "stato": IN_ATTESA,
                    # ⚠️ Vedi sopra: senza questa riga la scheda finisce in un
                    # vicolo cieco che nessun filtro mostra.
                    "pacchetto_id": False,
                    "motivo": testo}):
                continue
            # ⚠️⚠️ IL RIPIEGO PIU' STRETTO, e non e' prudenza generica: uno
            # dei cinque percorsi che arrivano qui e' proprio «la scrittura
            # delle righe e' fallita», e la causa tipica e' un vincolo su
            # `pacchetto_id`. Insistere con lo stesso campo che ha appena
            # rotto sarebbe insistere sull'unica cosa che sappiamo non
            # funzionare, e le righe resterebbero `sconosciuto` senza
            # pacchetto — cioe' CANDIDATE, e al giro dopo ripartirebbero
            # mentre Cdiscount forse le sta gia' lavorando. E' il difetto D2
            # del Compito 9, e la regola che ne e' uscita vale ancora: **il
            # messaggio non e' la protezione, il meccanismo lo e'**, e questo
            # secondo tentativo tocca meno cose del primo, quindi ha una
            # possibilita' vera di riuscire.
            #
            # ⚠️ Cosi' pero' la riga resta `in_attesa` col vecchio pacchetto
            # chiuso addosso, che e' proprio la combinazione senza uscita di
            # cui sopra: e' per questo che `ARENATE` prende ANCHE `in_attesa`.
            # La rete di sicurezza esiste perche' questo ramo esiste.
            if self._al_riparo(gruppo.write, {"stato": IN_ATTESA,
                                              "motivo": testo}):
                stretta += len(ids)
                continue
            fallita += len(ids)
        if stretta:
            _logger.error(
                "Cdiscount sul canale %s: su %s righe non si e' potuto "
                "staccare il pacchetto vecchio; restano «in attesa» con "
                "quello addosso e si vedono nel filtro «Senza verdetto, "
                "pacchetto chiuso». Motivo: %s",
                self.channel.display_name, stretta, motivo)
        if fallita:
            _logger.error(
                "Cdiscount sul canale %s: non si e' nemmeno potuto scrivere "
                "il motivo su %s delle %s righe di un pacchetto di esito "
                "ignoto. Motivo: %s", self.channel.display_name, fallita,
                len(righe), motivo)

    # ------------------------------------------------------------------
    # IL GIRO
    # ------------------------------------------------------------------
    def manda_schede(self, limite=None):
        """Manda a Cdiscount le schede pronte, a pacchetti.

        `limite` conta le SCHEDE e serve alla prima prova sul vero: si parte
        da una sola, si guarda sul portale, e solo dopo si manda il resto.

        L'ordine delle cose, e non e' negoziabile:

        1. il turno (un giro per volta su questo canale);
        2. le guardie — credenziali, canale di vendita, **categoria**, e
           almeno una riga con contenuti;
        3. la composizione: una riga che non si compone si SCARTA nominando
           il perche', e non ferma le altre;
        4. per ogni pacchetto: si scarica quel che e' in canna, si chiama, e
           **appena arriva il numero lo si scrive**, da solo, nel suo
           savepoint;
        5. solo DOPO le righe passano a `in_attesa` col loro pacchetto.
        """
        # ⚠️ Guardia 0 — il turno. Prima di tutto: due giri sovrapposti
        # leggerebbero le stesse righe e manderebbero due volte lo stesso
        # catalogo. La meccanica sta nella classe base, promossa li' apposta.
        self._prendi_il_turno(_("invio delle schede"))

        # Le guardie di configurazione, tutte prima di sporcare qualcosa.
        # ⚠️ La categoria per prima fra quelle che parlano del contenuto: e'
        # la piu' costosa da sbagliare, e non costa niente controllarla.
        categoria = self._categoria()
        client = self._client()
        # ⚠️ Il gettone si chiede ADESSO e non alla prima chiamata: se le
        # credenziali sono sbagliate si deve leggere «il gettone non e'
        # arrivato», non un esito ignoto che invita a rileggere un pacchetto
        # che non e' mai partito.
        self._gettone()

        Scheda = self.env["cdiscount.scheda"].sudo()
        dominio = self._candidate()
        candidate = Scheda.search(dominio)
        if not candidate:
            # ⚠️ Una UserError e non un successo a zero: «non c'e' niente da
            # mandare» detto in verde si legge «e' andato tutto bene», ed e'
            # la frase che nasconde 347 schede senza traduzione francese.
            raise UserError(_(
                "Sul canale «%s» non c'e' nessuna scheda pronta da mandare. "
                "Una scheda parte solo se ha titolo, descrizione e immagini "
                "(in francese) e non e' gia' partita: carica il file dei "
                "contenuti e guarda quante restano senza.")
                % self.channel.display_name)

        esito = {"mandate": 0, "pacchetti": 0, "scartate": 0, "rifiutate": 0,
                 "incerte": 0, "non_partite": 0, "rimaste": 0,
                 "categoria": categoria}
        motivi = {}
        fermata = None

        # --------------------------------------------------------------
        # 1) La composizione. Non tocca la rete: una riga che non si compone
        #    si scarta nominando il prodotto, e le altre proseguono.
        # --------------------------------------------------------------
        tetto = MAX_SCHEDE_PER_GIRO
        if limite:
            tetto = min(limite, MAX_SCHEDE_PER_GIRO)
        componibili = []
        troncato = False
        for riga in candidate:
            if len(componibili) >= tetto:
                troncato = True
                break
            try:
                corpo = self._corpo(riga, categoria)
            except ValueError as errore:
                motivo = str(errore)
                # ⚠️ Il ritorno di `_al_riparo` non si legge QUI, ed e'
                # deliberato: la riga e' scartata perche' non si compone, non
                # perche' la nota sia stata scritta. Se la nota non si scrive,
                # `_al_riparo` lascia il traceback nel registro di sistema.
                self._al_riparo(riga.write, {"motivo": motivo})
                esito["scartate"] += 1
                motivi[motivo] = motivi.get(motivo, 0) + 1
                continue
            componibili.append((riga, corpo))

        if troncato and not limite:
            fermata = _(
                "Il giro si e' fermato al tetto di %s schede per volta, per "
                "non farsi uccidere dal limite di tempo del worker — che si "
                "porterebbe via anche il numero dei pacchetti gia' partiti. "
                "Ripetere per continuare.") % MAX_SCHEDE_PER_GIRO

        if not componibili:
            # Tutte scartate: nessuna chiamata, ma il conto e i motivi si
            # scrivono lo stesso — sono la notizia.
            return self._chiudi(dominio, esito, motivi, fermata)

        # --------------------------------------------------------------
        # 2) I pacchetti.
        # --------------------------------------------------------------
        gruppi = pacchetti(componibili)
        partenza = time.monotonic()
        for gruppo in gruppi:
            if esito["pacchetti"] >= MAX_PACCHETTI_PER_GIRO:
                fermata = _(
                    "Il giro si e' fermato dopo %s pacchetti, per non farsi "
                    "uccidere dal limite di tempo del worker. Ripetere per "
                    "continuare.") % MAX_PACCHETTI_PER_GIRO
                break
            # ⚠️ Il tempo si guarda solo DOPO il primo pacchetto: altrimenti
            # un canale lento non manderebbe mai niente.
            if esito["pacchetti"] and (
                    time.monotonic() - partenza > SECONDI_PER_GIRO):
                fermata = _(
                    "Il giro si e' fermato da solo dopo %s secondi per non "
                    "farsi uccidere dal limite di tempo del worker — che "
                    "annullerebbe anche i numeri dei pacchetti gia' partiti. "
                    "Ripetere per continuare.") % SECONDI_PER_GIRO
                break

            righe = Scheda.browse([riga.id for riga, _corpo in gruppo])
            corpo = {CHIAVE_SCHEDE: [uno for _riga, uno in gruppo]}
            # ⚠️ Assegnato PRIMA di qualunque cosa che possa sollevare, cosi'
            # l'`except` generico lo trova sempre definito e puo' NOMINARLO
            # quando il guasto capita dopo che il numero e' arrivato. Senza,
            # l'unico posto in cui quel numero compariva era un
            # `_logger.error` che in quel salto non viene eseguito — e un
            # numero che non si sa e' un esito perso. (Stare fuori dal `try`
            # e' cosmetico: Python ha scope di funzione, non di blocco. La
            # proprieta' vera e' l'ordine, non la posizione.)
            numero = ""
            try:
                # ⚠️ SI SCARICA PRIMA DI CHIAMARE, ed e' una CINTURA
                # dichiarata. Il `cr.savepoint()` di Odoo scarica da solo
                # ENTRANDO, cioe' prima che il savepoint esista: una
                # scrittura rimasta in canna esploderebbe li', FUORI da
                # qualunque protezione, e la transazione andrebbe in stato
                # abortito proprio mentre sta per arrivare un numero da
                # scrivere. Oggi in canna non c'e' niente — le note degli
                # scarti sono gia' passate dai loro savepoint — e infatti il
                # banco non riesce a punire chi togliesse questa riga: e'
                # dichiarato fra le mutazioni sopravvissute. Resta perche' il
                # giorno in cui qualcuno aggiunge una scrittura qui sopra,
                # senza savepoint, il danno sarebbe il peggiore del modulo.
                self.env.flush_all()

                risposta = client.chiama("POST", API_SCHEDE, corpo)

                if getattr(risposta, "prevolo", False):
                    # ⚠️ NON E' PARTITO NIENTE, ed e' l'opposto dell'esito
                    # ignoto qui sotto benche' lo stato sia lo stesso 0.
                    # `chiama()` promette di non sollevare mai, e per
                    # mantenerla trasforma in risposta a stato 0 anche i due
                    # passi che stanno PRIMA della rete: il gettone non
                    # ottenuto e il carico non serializzabile. In quei due
                    # casi nessun byte ha lasciato questa macchina, quindi
                    # Cdiscount non puo' aver creato niente e non c'e' nessun
                    # doppione da temere: le righe restano come sono —
                    # candidate — e ripartono al prossimo giro. Congelarle
                    # `in_attesa` senza pacchetto, come fa il ramo incerto,
                    # le manderebbe nel limbo delle orfane e direbbe di
                    # RILEGGERE cosa c'e' la' fuori: fino a 10.000 righe da
                    # ripescare a mano per un guasto che sta tutto da questa
                    # parte. Vedi `RispostaCdiscount.prevolo`.
                    #
                    # ⚠️ Ci si ferma lo stesso (`break`): il guasto e' del
                    # nostro lato e si ripresenterebbe identico sul pacchetto
                    # dopo — il gettone e' lo stesso per tutto il giro.
                    esito["non_partite"] += len(gruppo)
                    fermata = _(
                        "Il giro si e' fermato PRIMA di chiamare Cdiscount: "
                        "%(perche)s. ⚠️ Non e' partito niente e nessuna "
                        "scheda e' nata la' fuori: e' un guasto dal nostro "
                        "lato. Queste %(quante)s schede NON sono state "
                        "toccate e ripartiranno al prossimo invio, senza "
                        "rischio di doppioni. Non c'e' niente da rileggere "
                        "su Cdiscount: si corregge la causa e si ripete.")
                    fermata = fermata % {"perche": risposta.messaggio,
                                         "quante": len(gruppo)}
                    _logger.error("Cdiscount sul canale %s: %s",
                                  self.channel.display_name, fermata)
                    # ⚠️ Il motivo si scrive, lo STATO no: e' cio' che le
                    # tiene candidate. Il messaggio non e' la protezione, il
                    # meccanismo lo e' — qui il meccanismo e' non scrivere.
                    self._al_riparo(righe.write, {"motivo": fermata})
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        fermata, str(corpo)[:MAX_PAYLOAD])
                    break

                incerta = self._causa_incerta(risposta)
                if incerta:
                    # ⚠️ ESITO IGNOTO: CI SI FERMA E NON SI RITENTA. Il
                    # pacchetto puo' essere stato preso lo stesso, e
                    # rimandarlo raddoppierebbe fino a 10.000 schede su un
                    # catalogo pubblico. Va RILETTO, non rimandato.
                    esito["incerte"] += len(gruppo)
                    fermata = _(
                        "Esito IGNOTO su un pacchetto di %(quante)s schede: "
                        "%(perche)s. Ci si ferma e non si ritenta: il "
                        "pacchetto puo' essere stato preso lo stesso, e "
                        "rimandarlo creerebbe le stesse schede due volte sul "
                        "catalogo. Va RILETTO — «%(dove)s» elenca le schede "
                        "nostre — prima di mandare altro.")
                    fermata = fermata % {"quante": len(gruppo),
                                         "perche": incerta,
                                         "dove": "GET %s" % API_SCHEDE_NOSTRE}
                    _logger.error("Cdiscount sul canale %s: %s",
                                  self.channel.display_name, fermata)
                    self._in_volo_senza_esito(righe, fermata)
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        fermata, str(corpo)[:MAX_PAYLOAD])
                    break

                if not risposta.ok:
                    # Rifiuto CERTO: il pacchetto non e' nato, e nessuna
                    # scheda con lui. Le righe restano candidate — e' il
                    # verso giusto: si corregge il dato e si rimanda.
                    motivo = self._motivo_stato(risposta.stato,
                                                risposta.messaggio)
                    esito["rifiutate"] += len(gruppo)
                    motivi[motivo] = motivi.get(motivo, 0) + len(gruppo)
                    self._al_riparo(righe.write, {"motivo": _(
                        "Cdiscount ha rifiutato il PACCHETTO INTERO in cui "
                        "questa scheda era: %s") % motivo})
                    # ⚠️ Anche una riga di REGISTRO passa dal savepoint. Un
                    # guasto scrivendo il registro non deve potersi portare
                    # via il numero di un pacchetto scritto poco fa: qui si e'
                    # dentro il giro, e i pacchetti precedenti sono gia'
                    # partiti davvero.
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        motivo, str(corpo)[:MAX_PAYLOAD])
                    continue

                numero = self._numero_pacchetto(risposta)
                if not numero:
                    # ⚠️ IL CASO PEGGIORE, ED E' GIA' SUCCESSO IN CASA: su
                    # Kaufland, il 2026-08-25, 165 offerte sono nate senza
                    # che ne conoscessimo l'identificativo. Qui e' anche piu'
                    # grave: Cdiscount ha PRESO il pacchetto, ci lavora, e
                    # senza il numero l'esito e' irrecuperabile fra tre
                    # giorni. Non si inventa un numero e non si ritenta.
                    esito["incerte"] += len(gruppo)
                    fermata = _(
                        "Cdiscount ha ACCETTATO un pacchetto di %(quante)s "
                        "schede ma la risposta non porta nessun numero di "
                        "pacchetto: %(corpo)s. Senza numero l'esito non e' "
                        "piu' recuperabile, e fra tre giorni non lo sara' "
                        "per nessuno. NON si rimanda: va guardato cosa c'e' "
                        "davvero la' fuori con «%(dove)s».") % {
                            "quante": len(gruppo),
                            "corpo": (risposta.testo or "")[:300]
                            or _("risposta vuota"),
                            "dove": "GET %s" % API_SCHEDE_NOSTRE}
                    _logger.error("Cdiscount sul canale %s: %s",
                                  self.channel.display_name, fermata)
                    self._in_volo_senza_esito(righe, fermata)
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        fermata, str(corpo)[:MAX_PAYLOAD])
                    break

                # ------------------------------------------------------
                # ⚠️⚠️ IL NUMERO, PRIMA DI QUALUNQUE ALTRA COSA.
                # Nel suo savepoint, da solo, e scaricato subito. Tutto cio'
                # che viene dopo puo' fallire: il numero deve restare.
                # ------------------------------------------------------
                adesso = fields.Datetime.to_datetime(fields.Datetime.now())
                scade = adesso + timedelta(days=GIORNI_ESITO)
                scritto = self._al_riparo(self._crea_pacchetto, numero,
                                          adesso, scade)
                pacchetto = (self._pacchetto_scritto(numero)
                             if scritto else None)
                if not pacchetto:
                    esito["incerte"] += len(gruppo)
                    fermata = _(
                        "Il pacchetto %(numero)s e' stato accettato da "
                        "Cdiscount ma il suo numero NON si e' potuto "
                        "scrivere in Odoo. E' scritto qui e nel registro di "
                        "sistema, e va copiato a mano prima che si perda: "
                        "l'esito di %(quante)s schede scade fra %(giorni)s "
                        "giorni. Non si manda altro.") % {
                            "numero": numero, "quante": len(gruppo),
                            "giorni": GIORNI_ESITO}
                    # ⚠️ La causa piu' probabile e' il vincolo
                    # `unique(channel_id, numero)`: quel numero su questo
                    # canale c'e' GIA'. Non si attaccano le righe a quella
                    # riga — non sappiamo se sia lo stesso pacchetto, e la
                    # sua finestra di tre giorni e' un'altra — ma lo si dice,
                    # perche' cambia completamente cosa andare a guardare.
                    if self._pacchetto_scritto(numero):
                        fermata += _(
                            " ⚠️ Un pacchetto con questo numero risulta già "
                            "registrato su questo canale: o Cdiscount ha "
                            "ridato lo stesso numero, o questo invio è un "
                            "doppione di uno appena fatto.")
                    # ⚠️ `_logger.error` con il numero dentro: il registro di
                    # sistema e' l'unica cosa che un rollback non tocca.
                    _logger.error(
                        "Cdiscount sul canale %s: PACCHETTO %s ACCETTATO E "
                        "NON SCRITTO. Scade il %s. Schede: %s",
                        self.channel.display_name, numero, scade,
                        ", ".join(_testo(r.codice) for r, _c in gruppo[:50]))
                    self._in_volo_senza_esito(righe, fermata)
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        fermata, str(corpo)[:MAX_PAYLOAD], numero)
                    break

                # Il numero e' al sicuro: da qui in poi si conta.
                esito["pacchetti"] += 1

                # ⚠️ E QUESTA E' «L'ALTRA COSA»: se esplode, il numero resta.
                if not self._al_riparo(righe.write, {
                        "stato": IN_ATTESA,
                        "pacchetto_id": pacchetto.id,
                        # La nota vecchia si toglie: una riga ripartita che
                        # tiene addosso il motivo del rifiuto precedente si
                        # legge come rifiutata di nuovo.
                        "motivo": False}):
                    fermata = _(
                        "Il pacchetto %(numero)s e' partito e il suo numero "
                        "e' salvo, ma le %(quante)s schede che conteneva non "
                        "si sono potute segnare «in attesa». Il raccoglitore "
                        "leggera' l'esito del pacchetto ma non trovera' le "
                        "righe a cui attribuirlo: vanno guardate a mano "
                        "prima di rimandarle. Non si manda altro.") % {
                            "numero": numero, "quante": len(gruppo)}
                    _logger.error("Cdiscount sul canale %s: %s",
                                  self.channel.display_name, fermata)
                    # ⚠️ E SI TOLGONO DAI CANDIDATI LO STESSO. Era il difetto
                    # peggiore di questo ramo: il numero restava — quello si'
                    # — ma le righe restavano `sconosciuto` SENZA pacchetto,
                    # cioe' esattamente il profilo di `_candidate()`. Chi
                    # rileggeva la notifica («Restano da mandare: 1») e
                    # ricliccava le rimandava in un pacchetto nuovo mentre
                    # Cdiscount stava ancora lavorando il primo: doppioni su
                    # un catalogo pubblico, cioe' il danno che questo compito
                    # esiste per impedire.
                    #
                    # ⚠️ L'inversione era la parte istruttiva: tutti i rami
                    # INCERTI le toglievano dai candidati, e l'unico a
                    # lasciarle dentro era il ramo CERTO — quello in cui il
                    # pacchetto e' partito davvero e il numero e' in banca
                    # dati. E il messaggio non e' la protezione: il
                    # meccanismo lo e'. La scrittura qui e' piu' stretta di
                    # quella appena fallita (niente `pacchetto_id`), quindi
                    # ha una possibilita' vera di riuscire; se fallisce
                    # anche lei, `_in_volo_senza_esito` lo grida.
                    self._in_volo_senza_esito(righe, fermata)
                    self._al_riparo(
                        self._registra, "cdiscount_manda_pacchetto", "error",
                        fermata, None, numero)
                    break

                esito["mandate"] += len(gruppo)
                self._al_riparo(
                    self._registra, "cdiscount_manda_pacchetto", "success",
                    _("Pacchetto %(numero)s: %(quante)s schede, categoria "
                      "%(categoria)s. L'esito scade il %(scade)s.")
                    % {"numero": numero, "quante": len(gruppo),
                       "categoria": categoria,
                       "scade": self._ora_locale(scade)},
                    str(corpo)[:MAX_PAYLOAD], numero)
            except Exception as errore:  # noqa: BLE001
                # ⚠️ Qui NON c'e' un savepoint attorno al gruppo, ed e' la
                # scelta portante di tutto il metodo: un savepoint di gruppo
                # riporterebbe indietro ANCHE la scrittura del numero quando
                # qualcosa fallisce dopo, che e' esattamente il danno da
                # evitare.
                #
                # ⚠️ Cosa si prende, allora. Quasi sempre un guasto di Python
                # nostro, che la transazione non la tocca: ogni SCRITTURA di
                # questo blocco passa da `_al_riparo`, cioe' ha gia' il suo
                # savepoint, e le letture del pacchetto appena scritto pure
                # (`_pacchetto_scritto`). **Ma non e' vero che qui non possa
                # arrivare una transazione abortita**, e la riga che lo rende
                # possibile e' UNA SOLA: il `self.env.flush_all()` in testa al
                # `try`, che e' l'unica istruzione di database di tutto il
                # blocco a stare FUORI da `_al_riparo` — vedi il commento che
                # la accompagna, che dice la stessa cosa dall'altra parte.
                #
                # ⚠️ E in quel caso si esce comunque bene, ma per un altro
                # motivo: le scritture qui sotto passano tutte da
                # `_al_riparo`, e un savepoint e' proprio cio' che RIMETTE IN
                # PIEDI una transazione abortita. Quindi la nota arriva sulle
                # righe e la riga di registro si scrive. Chi togliesse
                # `_al_riparo` da una di queste, credendo che «tanto qui la
                # transazione e' sana», scoprirebbe che non lo e' — e il
                # messaggio che nomina il pacchetto sparirebbe proprio nel
                # caso peggiore.
                esito["incerte"] += len(gruppo)
                fermata = _(
                    "Il giro si e' interrotto su un pacchetto di %(quante)s "
                    "schede (%(tipo)s: %(errore)s). Non si sa se Cdiscount "
                    "l'abbia preso: NON si rimanda, si guarda con "
                    "«%(dove)s».") % {
                        "quante": len(gruppo), "tipo": type(errore).__name__,
                        "errore": errore,
                        "dove": "GET %s" % API_SCHEDE_NOSTRE}
                if numero:
                    # ⚠️ Il numero c'era gia': allora questo non e' piu' un
                    # esito ignoto, e' un pacchetto CERTO di cui si rischia di
                    # perdere l'appiglio. Va scritto nel messaggio che una
                    # persona legge, non solo nel registro di sistema.
                    fermata += _(
                        " ⚠️ Il numero di pacchetto era già arrivato ed è "
                        "%(numero)s: l'esito di queste schede scade fra "
                        "%(giorni)s giorni e va cercato con quel numero.") % {
                            "numero": numero, "giorni": GIORNI_ESITO}
                _logger.exception(
                    "Cdiscount sul canale %s: giro interrotto su un "
                    "pacchetto di %s schede. Numero di pacchetto: %s",
                    self.channel.display_name, len(gruppo),
                    numero or "non ancora arrivato")
                self._in_volo_senza_esito(righe, fermata)
                break

        return self._chiudi(dominio, esito, motivi, fermata)

    # ------------------------------------------------------------------
    def _chiudi(self, dominio, esito, motivi, fermata):
        """Tira le somme e lascia una riga nel registro.

        ⚠️ Dentro il proprio savepoint, e con la rete sotto: se i conti o il
        registro esplodono, i pacchetti partiti restano partiti e i loro
        numeri restano scritti. L'esito grezzo finisce nel registro di
        sistema, che un rollback non tocca. Stessa forma di
        `kaufland._chiudi_creazione`.
        """
        esito["fermata"] = fermata or False
        try:
            with self.env.cr.savepoint():
                Scheda = self.env["cdiscount.scheda"].sudo()
                esito["rimaste"] = Scheda.search_count(dominio)
                verde = not (esito["incerte"] or esito["rifiutate"]
                             or esito["scartate"]
                             or esito.get("non_partite") or fermata)
                pezzi = [
                    _("Schede mandate: %(mandate)s in %(pacchetti)s "
                      "pacchetti (categoria %(categoria)s).")
                    % {"mandate": esito["mandate"],
                       "pacchetti": esito["pacchetti"],
                       "categoria": esito["categoria"]},
                    _("Scartate prima di partire: %s.") % esito["scartate"],
                    _("In pacchetti rifiutati: %s.") % esito["rifiutate"],
                    _("Di esito ignoto: %s.") % esito["incerte"],
                    # ⚠️ SI SCRIVE SEMPRE, anche a zero, e sta ACCANTO
                    # all'esito ignoto apposta: sono i due numeri che lo
                    # stesso stato 0 confondeva, e leggerli vicini e' cio'
                    # che insegna la differenza. «Non partite» vuol dire che
                    # il guasto e' dal nostro lato e non c'e' niente da
                    # guardare su Cdiscount.
                    _("Non partite (guasto prima della rete): %s.")
                    % esito.get("non_partite", 0),
                    _("Restano da mandare: %s.") % esito["rimaste"],
                ]
                if fermata:
                    pezzi.append(fermata)
                # ⚠️ I PIU' FREQUENTI PRIMA, E CON UN TETTO. Senza, uno
                # scarto di composizione per ogni scheda — e ognuno NOMINA il
                # suo prodotto, quindi sono tutti diversi — scriverebbe
                # 10.000 righe in un solo campo Text. L'ordine per frequenza
                # e' cio' che rende il tetto innocuo: quel che resta fuori e'
                # sempre la coda rara, e quanta ne resta fuori si DICE.
                ordinati = sorted(motivi.items(), key=lambda kv: -kv[1])
                for motivo, quante in ordinati[:MAX_MOTIVI_NEL_REGISTRO]:
                    pezzi.append("%s × %s" % (quante, motivo))
                restano = len(ordinati) - MAX_MOTIVI_NEL_REGISTRO
                if restano > 0:
                    # ⚠️ Il rimando dice DOVE SONO DAVVERO, e non «nel
                    # registro del server»: gli scarti di composizione non
                    # passano dal log: ognuno viene scritto sul campo
                    # «Motivo» della sua riga. Rimandare a un posto che non
                    # li contiene e' una bugia con la faccia di un aiuto —
                    # e' il difetto gia' pagato nella finestra dei contenuti.
                    pezzi.append(_(
                        "… e altri %(quanti)s motivi diversi, non scritti "
                        "qui: sono %(schede)s schede in tutto. Ognuna porta "
                        "il suo motivo sulla propria riga, nella pagina "
                        "«Schede Cdiscount».")
                        % {"quanti": restano,
                           "schede": sum(q for _m, q in
                                         ordinati[MAX_MOTIVI_NEL_REGISTRO:])})
                self._registra("cdiscount_manda_schede",
                               "success" if verde else "error",
                               "\n".join(pezzi))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Cdiscount sul canale %s: la chiusura del giro e' fallita. "
                "Esito grezzo: %s", self.channel.display_name, esito)
            esito.setdefault("rimaste", 0)
            esito["chiusura_fallita"] = 1
        return esito

    # ------------------------------------------------------------------
    # IL RACCOGLITORE — l'esito, prodotto per prodotto
    #
    # ⚠️ LE TRE REGOLE DI QUESTA META' DEL MODULO, e nessuna e' generica.
    #
    # 1. **«In lavorazione» non e' un guasto.** Cdiscount sta ancora
    #    lavorando il pacchetto: si lascia APERTO e si ripassa. **Non si
    #    scrive niente** — ne' sulle righe ne' sul pacchetto. Scrivere un
    #    verdetto adesso vorrebbe dire inventarlo.
    #
    # 2. ⚠️ **Un rapporto che nomina meno prodotti di quanti ne sono partiti
    #    NON chiude in successo.** Le mancanti si contano, si dicono, e le
    #    loro righe **restano `in_attesa`**: il pacchetto resta APERTO, cosi'
    #    il giro dopo lo ripesca e — se nessuno lo legge entro tre giorni —
    #    la scadenza (Compito 11) lo chiude dicendo «non lo so», che e' la
    #    verita'. Chiuderlo in verde lascerebbe quelle righe **senza verdetto
    #    per sempre**: non tornerebbero fra i candidati dell'invio
    #    (`_candidate` vuole `sconosciuto` SENZA pacchetto, oppure
    #    `rifiutato`), e con 10.000 schede per pacchetto nessuno se ne
    #    accorgerebbe.
    #
    # 3. ⚠️ **Un savepoint PER PACCHETTO, e i conti si sommano DOPO.** Un
    #    errore del database mette la transazione in stato abortito: da li'
    #    ogni istruzione fallisce e il commit finale diventa un **rollback
    #    silenzioso** che si porta via gli esiti gia' raccolti — mentre il
    #    registro dice «raccolti: N». Il savepoint rimette in piedi la
    #    transazione e isola il pacchetto guasto dagli altri. E i contatori
    #    sono variabili Python, che **nessun rollback tocca**: se
    #    `_raccogli_uno` li aggiornasse mentre lavora, un guasto a meta'
    #    lascerebbe i numeri gonfiati esattamente come il registro bugiardo
    #    che questa regola esiste per impedire. Percio' rende i suoi conti a
    #    chi chiama, e chi chiama li somma **solo dopo che il savepoint e'
    #    uscito bene**.
    # ------------------------------------------------------------------
    def _dominio_aperti(self):
        """I pacchetti di schede di questo canale che aspettano un esito.

        ⚠️ Scritto UNA volta: lo usano il giro (per sapere chi interrogare) e
        la chiusura (per dire quanti ne restano). Due copie che divergono
        farebbero dire alla notifica «restano 0» mentre il giro dopo ne trova
        tre.

        ⚠️ E filtra sul TIPO. Oggi i pacchetti sono tutti di schede, ma le
        offerte sono la Consegna 2 e avranno il loro giro asincrono: senza
        questo filtro, il giorno in cui nascono, il raccoglitore delle schede
        andrebbe a chiedere il loro esito a
        `GET /products-integration-reports` — che di quei pacchetti non sa
        niente — e li terrebbe aperti fino alla scadenza.
        """
        return [("channel_id", "=", self.channel.id),
                ("tipo", "=", TIPO_SCHEDE),
                ("stato", "=", APERTO)]

    # ------------------------------------------------------------------
    # LA SCADENZA — la ragione per cui questa meta' del modulo esiste
    #
    # ⚠️ Un pacchetto il cui esito non si raccoglie entro tre giorni e' perso
    # PER SEMPRE: non c'e' un archivio, non c'e' un secondo tentativo, non
    # c'e' nessuno che ce lo dica. Con 10.000 schede per pacchetto non e' una
    # scheda a sparire, e' un catalogo — in silenzio.
    #
    # Due comportamenti, e sono diversi:
    #
    # - **in scadenza** (meno di `ORE_AVVISO` ore): si AVVISA una persona, una
    #   volta sola. Non si tocca niente: il pacchetto e' ancora buono, e c'e'
    #   ancora tempo per guardarlo;
    # - **scaduto**: si CHIUDE, si scrive l'errore una volta sola, e le sue
    #   righe passano da `in_attesa` a `sconosciuto`.
    # ------------------------------------------------------------------
    def _chiudi_gli_scaduti(self, aperti, adesso, esito, note):
        """Chiude i pacchetti oltre i tre giorni. Rende quelli ancora vivi.

        ⚠️ **GIRA PRIMA DI QUALUNQUE COSA IN RETE**, e non e' una preferenza
        di stile: un pacchetto scaduto che resta APERTO viene reinterrogato a
        ogni giro **per sempre**. Occupa uno dei posti di
        `MAX_PACCHETTI_PER_RACCOLTA` — ed e' precisamente la FAME descritta
        li' sopra, perche' `_order` mette in cima chi scade prima, cioe'
        proprio lui —, consuma il tempo del giro, e se il canale ha una
        guardia anti-doppio-invio (il turno) la tiene occupata piu' a lungo
        del dovuto. E' un difetto gia' pagato sulla piattaforma esterna.

        ⚠️ **Le righe passano a `sconosciuto`, NON a `rifiutato`.** Non
        sappiamo se Cdiscount le abbia accettate: dire «rifiutato» le
        rimetterebbe fra i candidati dell'invio (vedi `_candidate`) e le
        manderebbe a rifarsi **quando forse esistono gia'** — doppioni su un
        catalogo pubblico, che da qui non si tolgono. `sconosciuto` **con il
        pacchetto ancora attaccato** e' esattamente lo stato che i candidati
        NON prendono: restano ferme, rosse, e le guarda una persona.

        ⚠️ E lo `sconosciuto` che si scrive qui e' `SCONOSCIUTO_SCHEDA`, il
        vocabolario degli stati di una SCHEDA — non lo `SCONOSCIUTO` di
        `cdiscount_rapporto`, che dice tutt'altro (lo stato di un RAPPORTO
        illeggibile). Le due parole coincidono per caso; vedi il commento in
        testa a `models/cdiscount_scheda.py`.
        """
        vivi = []
        for pacchetto in aperti:
            # ⚠️ Numero e scadenza si leggono DENTRO il savepoint e si tengono
            # in variabili Python: sono letture SQL come le altre, e su una
            # transazione gia' abortita esploderebbero senza protezione. Il
            # gestore d'errore qui sotto puo' cosi' NOMINARE il pacchetto
            # senza tornare a chiedere niente al database — che e' la stessa
            # eccezione che sta sopravvivendo.
            numero = ""
            scade = None
            scaduto = False
            quante = 0
            try:
                # ⚠️ UN SAVEPOINT PER PACCHETTO. Catturare un errore del
                # database in Python NON salva la transazione: PostgreSQL la
                # mette in stato ABORTITO, da li' ogni istruzione fallisce e
                # il commit finale diventa un ROLLBACK silenzioso — che si
                # porterebbe via le chiusure gia' fatte mentre il registro
                # dice «scaduti: N». E il rollback e' anche cio' che rende la
                # chiusura ATOMICA: o il pacchetto e' chiuso E le sue righe
                # dicono «non lo so», o non e' successo niente e il giro dopo
                # ci riprova.
                with self.env.cr.savepoint():
                    numero = _testo(pacchetto.numero)
                    scade = fields.Datetime.to_datetime(pacchetto.scade_il)
                    # ⚠️ Una scadenza che non si sa leggere NON fa scadere
                    # niente. Chiudere su un valore illeggibile butterebbe via
                    # l'esito di un pacchetto che magari e' ancora buono, e
                    # sarebbe irreversibile; lasciarlo aperto lo fa comparire
                    # nel filtro «Oltre la scadenza, ancora aperti», che e'
                    # dove una persona lo vede.
                    scaduto = bool(scade and scade <= adesso)
                    if scaduto:
                        quante = self._scade_uno(pacchetto, numero, scade)
            except Exception as errore:  # noqa: BLE001
                # ⚠️ Un pacchetto che esplode non ferma gli altri, ed e' il
                # savepoint a renderlo vero. E NON entra fra i vivi: non
                # sappiamo nemmeno se sia scaduto, e interrogarlo adesso
                # significherebbe chiedere alla rete prima di aver deciso —
                # cioe' l'ordine che questo metodo esiste per difendere.
                esito["guasti"] += 1
                nota = _(
                    "Pacchetto %(numero)s: non si e' potuto nemmeno "
                    "controllare se fosse scaduto (%(tipo)s: %(errore)s). "
                    "Niente e' stato scritto e il pacchetto resta aperto: si "
                    "riprova al giro dopo. ⚠️ Se questo si ripete, l'esito "
                    "scade senza che nessuno lo chiuda.") % {
                        "numero": numero or _("(non letto)"),
                        "tipo": type(errore).__name__, "errore": errore}
                _logger.exception(
                    "Cdiscount sul canale %s: controllo della scadenza "
                    "interrotto sul pacchetto %s.",
                    self.channel.display_name, numero or "numero non letto")
                note.append(nota)
                continue

            if not scaduto:
                vivi.append(pacchetto)
                continue

            # ⚠️ SOLO ADESSO i conti si muovono: un contatore Python
            # aggiornato dentro il savepoint sopravvivrebbe al rollback e
            # direbbe di aver chiuso cio' che e' tornato indietro.
            esito["scaduti"] += 1
            esito["senza_verdetto"] += quante
            messaggio = _(
                "⚠️ Pacchetto %(numero)s SCADUTO il %(scade)s senza esito. "
                "Cdiscount non ha piu' niente da dirci su questo pacchetto: "
                "non esiste piu' nessun modo di sapere cosa ne abbia fatto. "
                "Il pacchetto e' chiuso e %(quante)s schede restano senza "
                "verdetto. NON vanno rimandate alla cieca: se esistono gia' "
                "la' fuori, il rinvio le duplica su un catalogo pubblico. Si "
                "guarda con GET %(dove)s, una per una, e si decide a mano.") \
                % {"numero": numero, "scade": self._ora_locale(scade),
                   "quante": quante, "dove": API_SCHEDE_NOSTRE}
            _logger.error("Cdiscount sul canale %s: %s",
                          self.channel.display_name, messaggio)
            note.append(messaggio)
            # ⚠️ La riga di registro sta FUORI dal savepoint della chiusura, e
            # nel suo: se scriverla fallisse, la chiusura gia' fatta non deve
            # tornare indietro con lei — un pacchetto riaperto verrebbe
            # richiuso al giro dopo e l'errore si registrerebbe due volte.
            # ⚠️ **E si registra UNA VOLTA SOLA** senza bisogno di nessuna
            # bandiera: il pacchetto e' gia' passato a `scaduto`, quindi
            # `_dominio_aperti()` non lo ripesca mai piu'.
            self._al_riparo(self._registra, "cdiscount_pacchetto_scaduto",
                            "error", messaggio, None, numero)
        return vivi

    def _scade_uno(self, pacchetto, numero, scade):
        """Chiude UN pacchetto scaduto. Rende quante righe restano al buio.

        ⚠️ **Il pacchetto si scrive per PRIMO, le righe dopo.** E' l'opposto
        di `_raccogli_uno`, dove le righe vanno scritte prima perche' un
        pacchetto chiuso con delle righe senza verdetto e' schede perse. Qui
        il verdetto non arrivera' mai piu' e la cosa urgente e' l'altra:
        togliere il pacchetto dal giro. Le due scritture stanno comunque
        nello stesso savepoint — o tutte e due o nessuna — quindi l'ordine
        non cambia lo stato finale; cambia cosa succede se il processo viene
        UCCISO nel mezzo (il worker ha `limit_time_real = 120`), e in quel
        caso e' meglio un pacchetto chiuso con delle righe ancora «in
        attesa», che si vedono, di un pacchetto aperto per sempre.
        """
        pacchetto.write({"stato": SCADUTO})
        Scheda = self.env["cdiscount.scheda"].sudo()
        # ⚠️ SOLO le righe ancora `in_attesa`. Un pacchetto puo' essere stato
        # letto a meta' in un giro precedente (rapporto parziale, regola 2):
        # quelle righe hanno gia' il loro verdetto vero, «riuscito» o
        # «rifiutato», e riscriverlo a «non lo so» butterebbe via l'unica
        # cosa che Cdiscount ci ha detto davvero.
        righe = Scheda.search([("pacchetto_id", "=", pacchetto.id),
                               ("stato", "=", IN_ATTESA)])
        if righe:
            # ⚠️ Niente `controllato_il`: nessuno ha controllato niente. Una
            # data su una riga senza verdetto si legge «l'ho guardata e va
            # bene», ed e' la stessa ragione per cui `_raccogli_uno` non la
            # scrive sulle mancanti.
            righe.write({
                "stato": SCONOSCIUTO_SCHEDA,
                "motivo": _(
                    "Il pacchetto %(numero)s e' scaduto il %(scade)s senza "
                    "che Cdiscount abbia mai dato un esito: NON sappiamo se "
                    "questa scheda esista su Cdiscount o no. Non va "
                    "rimandata alla cieca — se esiste gia', il rinvio ne crea "
                    "un doppione su un catalogo pubblico, e da qui non si "
                    "toglie. Si guarda con GET %(dove)s e si decide a mano.")
                % {"numero": numero, "scade": self._ora_locale(scade),
                   "dove": API_SCHEDE_NOSTRE},
            })
        return len(righe)

    def _avvisa_chi_scade(self, aperti, adesso, esito, note):
        """Avvisa UNA persona, UNA volta, sui pacchetti alle ultime ore.

        ⚠️ **Anche questo gira prima della rete**, e il motivo qui e' un
        altro: l'avviso non deve stare dentro nessuna transazione tenuta
        aperta attraverso una chiamata HTTP. Una chiamata dura fino ad
        `ATTESA` secondi e puo' finire uccisa dal worker: l'attivita' scritta
        prima di quella chiamata sopravvive alla chiusura del giro, quella
        scritta dopo se ne va col rollback — e con lei l'unico avviso.
        """
        limite = adesso + timedelta(hours=ORE_AVVISO)
        for pacchetto in aperti:
            # ⚠️ Le tre letture stanno in un savepoint, e i loro risultati in
            # variabili Python che nessun rollback tocca: servono a decidere
            # FUORI dal savepoint cosa e' successo dentro.
            visto = {"serve": False, "numero": "", "scade": None}

            def _guarda(pacchetto=pacchetto, visto=visto):
                visto["numero"] = _testo(pacchetto.numero)
                if pacchetto.avvisato_scadenza:
                    return
                scade = fields.Datetime.to_datetime(pacchetto.scade_il)
                if not scade or scade > limite:
                    return
                visto["serve"] = True
                visto["scade"] = scade

            if not self._al_riparo(_guarda) or not visto["serve"]:
                continue

            fatto = self._manda_avviso(
                pacchetto,
                # ⚠️ IL SOMMARIO E' LA CHIAVE DELL'ANTI-DOPPIONE E NOMINA IL
                # PACCHETTO. Cambiarne il testo — anche solo un accento — fa
                # ripartire un secondo avviso su ogni pacchetto gia'
                # avvisato.
                "Cdiscount: il pacchetto %s scade fra poche ore"
                % visto["numero"],
                _("L'esito del pacchetto Cdiscount %(numero)s scade il "
                  "%(scade)s, cioe' fra meno di %(ore)s ore. Passata quella "
                  "data non esiste piu' nessun modo di sapere cosa Cdiscount "
                  "abbia fatto delle schede che c'erano dentro: non c'e' un "
                  "archivio e non c'e' un secondo tentativo. Se il "
                  "raccoglitore automatico non ha ancora letto il rapporto, "
                  "va guardato adesso.")
                % {"numero": visto["numero"],
                   "scade": self._ora_locale(visto["scade"]),
                   "ore": ORE_AVVISO},
                # La scadenza vera come termine, cosi' l'attivita' si ordina
                # da sola fra le altre invece di sembrare roba di oggi
                # qualunque.
                self._giorno_locale(visto["scade"]),
                "avvisato_scadenza")

            if not fatto["partito"]:
                nota = _(
                    "⚠️ Pacchetto %(numero)s: scade fra meno di %(ore)s ore e "
                    "NON si e' potuto creare l'avviso. Il pacchetto resta "
                    "segnato come «non ancora avvisato» e si riprova al giro "
                    "dopo, ma se l'avviso non parte mai nessuno sapra' che "
                    "sta per scadere.") % {"numero": visto["numero"],
                                           "ore": ORE_AVVISO}
                _logger.error("Cdiscount sul canale %s: %s",
                              self.channel.display_name, nota)
                # ⚠️ SI CONTA, e non e' un doppione della nota. La riga finale
                # del giro NON si scrive quando c'era solo da aspettare
                # (`solo_attesa`), e un avviso non partito su un pacchetto in
                # lavorazione e' esattamente quel caso: senza questo contatore
                # la nota resterebbe in una lista che nessuno stampa mai, e
                # l'unico avviso della consegna sarebbe fallito IN SILENZIO.
                esito["avvisi_falliti"] += 1
                note.append(nota)
                continue
            if fatto["nuova"]:
                esito["avvisati"] += 1
                note.append(_(
                    "Pacchetto %(numero)s: scade fra meno di %(ore)s ore. E' "
                    "stata creata un'attivita' per guardarlo prima che "
                    "l'esito diventi irrecuperabile.")
                    % {"numero": visto["numero"], "ore": ORE_AVVISO})
            if not fatto["segnata"]:
                note.append(_(
                    "⚠️ Pacchetto %(numero)s: l'avviso e' partito ma non si e' "
                    "potuto segnare come avvisato. Non si ripetera' comunque "
                    "(l'attivita' esistente viene riconosciuta), ma la "
                    "schermata continuera' a mostrarlo fra i «non ancora "
                    "avvisati».") % {"numero": visto["numero"]})

    def _avvisa_rapporto_muto(self, pacchetto, numero, quante, estranee,
                              scade, esito, note):
        """⚠️ Il rapporto e' PRONTO e non nomina NEMMENO UNA delle nostre.

        **Non e' un rapporto a meta': e' la firma di una chiave letta col
        nome sbagliato**, ed e' l'unica diagnosi che non si puo' lasciare in
        un registro. Un rapporto parziale (alcune nominate, altre no) e' una
        notizia su QUEL pacchetto; un rapporto che non nomina nessuno e' una
        notizia su TUTTI: se il nome — o il valore — con cui Cdiscount
        rimanda il nostro codice non e' quello che leggiamo, **ogni** pacchetto
        tornera' cosi', per sempre, e nessuna scheda avra' mai un verdetto.
        Con 10.000 schede per pacchetto, il registro direbbe «senza verdetto»
        ogni mezz'ora e nessuno lo leggerebbe.

        ⚠️ **Va chiamato FUORI dal savepoint del pacchetto.** Il pacchetto
        muto NON si chiude — le sue righe restano `in_attesa` (regola 2) — e
        se l'attivita' stesse dentro quel savepoint tornerebbe indietro con
        qualunque guasto successivo. E' la stessa disciplina dell'avviso di
        scadenza.

        ⚠️ E si dice UNA VOLTA per pacchetto, con la bandiera: qui il
        pacchetto resta APERTO, quindi il raccoglitore ci ripassa ogni
        mezz'ora per tre giorni. La sola ricerca per sommario non basterebbe
        — in Odoo segnare un'attivita' come fatta la CANCELLA, e mezz'ora dopo
        ne nascerebbe un'altra.

        ⚠️ **Il testo si biforca su `estranee`, e non e' cosmesi.** Un
        rapporto che non nomina NESSUNO punta al NOME della chiave, e quello
        si' riguarda ogni pacchetto. Un rapporto che nomina solo codici che
        non riconosciamo ha parlato: o il valore torna trasformato (di nuovo
        ogni pacchetto), o quel rapporto non e' di questo pacchetto (e allora
        riguarda lui solo). Dire «riguarda TUTTI i pacchetti» anche nel
        secondo caso e' un'affermazione che il modulo non puo' sostenere, e
        manda a cercare un difetto di sistema dove ce n'e' uno locale.
        """
        visto = {"serve": False}

        def _guarda(pacchetto=pacchetto, visto=visto):
            visto["serve"] = not pacchetto.avvisato_rapporto_muto

        if not self._al_riparo(_guarda) or not visto["serve"]:
            return

        fatto = self._manda_avviso(
            pacchetto,
            "Cdiscount: il rapporto del pacchetto %s non nomina nessuna "
            "scheda" % numero,
            _("Il rapporto del pacchetto Cdiscount %(numero)s e' arrivato "
              "COMPLETO e non nomina nemmeno una delle %(quante)s schede che "
              "erano partite. ⚠️ Non e' un rapporto a meta'.\n\n"
              "%(diagnosi)s\n\n"
              "Il codice con cui riconosciamo una scheda nel rapporto si "
              "cerca sotto due nomi, «%(chiavi)s», presi dalla "
              "documentazione: nessuno dei due e' mai stato visto su un "
              "rapporto vero.\n\n"
              "Cosa fare: aprire il registro delle operazioni, cercare la "
              "riga di questo pacchetto e leggere il corpo del rapporto come "
              "e' arrivato. Se il rapporto nomina dei codici che non "
              "riconosciamo, il registro li elenca come «voci estranee». Nel "
              "frattempo le %(quante)s schede restano «in attesa» e il "
              "pacchetto resta aperto: non e' stato dato nessun verdetto e "
              "non e' stato rimandato niente.")
            % {"numero": numero, "quante": quante,
               "diagnosi": self._diagnosi_muto(estranee),
               "chiavi": "», «".join(CHIAVI_CODICE)},
            # ⚠️ Il termine e' la scadenza del pacchetto, non «oggi»: e' la
            # data entro cui la cosa va guardata perche' serva ancora a
            # qualcosa.
            self._giorno_locale(scade),
            "avvisato_rapporto_muto")

        if not fatto["partito"]:
            nota = _(
                "⚠️ Pacchetto %(numero)s: il rapporto non nomina nessuna "
                "scheda e NON si e' potuto creare l'avviso. Resta scritto "
                "solo qui, ed e' %(etichetta)s."
            ) % {"numero": numero,
                 "etichetta": self._etichetta_muto(estranee)}
            _logger.error("Cdiscount sul canale %s: %s",
                          self.channel.display_name, nota)
            esito["avvisi_falliti"] += 1
            note.append(nota)
            return
        if fatto["nuova"]:
            esito["avvisati"] += 1
            note.append(_(
                "⚠️ Pacchetto %(numero)s: il rapporto non nomina NESSUNA "
                "delle %(quante)s schede partite. E' stata creata "
                "un'attivita': non e' un rapporto a meta', e' %(etichetta)s.")
                % {"numero": numero, "quante": quante,
                   "etichetta": self._etichetta_muto(estranee)})
        if not fatto["segnata"]:
            note.append(_(
                "⚠️ Pacchetto %(numero)s: l'avviso sul rapporto muto e' "
                "partito ma non si e' potuto segnare. Non si ripetera' "
                "comunque, finche' l'attivita' resta aperta.")
                % {"numero": numero})

    @staticmethod
    def _etichetta_muto(estranee):
        """La diagnosi del rapporto muto in una riga, per le NOTE del giro.

        ⚠️ Sta accanto a `_diagnosi_muto` e si biforca sulla stessa cosa: le
        note del giro finiscono nel registro finale, che una persona legge
        ACCANTO alla riga del pacchetto e all'attivita'. Se quelle dicono
        «nel VALORE» e la nota del giro dice «nel NOME», il modulo si
        contraddice da solo — ed e' esattamente quel che faceva.
        """
        if estranee:
            return _("il sospetto di una chiave sbagliata nel VALORE (il "
                     "riferimento torna trasformato), oppure di un rapporto "
                     "che non e' di questo pacchetto")
        return _("il sospetto di una chiave sbagliata nel NOME, e allora "
                 "riguarda ogni pacchetto")

    @staticmethod
    def _diagnosi_muto(estranee):
        """Perche' il rapporto non nomina nessuna delle nostre. Due strade.

        ⚠️ Sta in un metodo suo perche' la stessa distinzione la fa anche la
        riga di registro (`_riga_rapporto`): due copie divergono, e a
        divergere sarebbero due testi che una persona confronta — l'attivita'
        e la riga a cui l'attivita' la manda.
        """
        if estranee:
            return _(
                "Il rapporto NOMINA %(quante)s codici, ma non ne "
                "riconosciamo nessuno. Le spiegazioni sono due, e portano a "
                "guardare cose diverse:\n"
                "  1) il riferimento ci torna TRASFORMATO — con un prefisso, "
                "tagliato, rinumerato: e' una chiave sbagliata nel VALORE, e "
                "allora ogni pacchetto tornera' cosi', per sempre;\n"
                "  2) questo rapporto non e' di questo pacchetto — e allora "
                "riguarda lui solo.\n"
                "Si distinguono a occhio: se i codici elencati nel registro "
                "assomigliano ai nostri e' la prima, se sono di altri "
                "prodotti e' la seconda."
            ) % {"quante": estranee}
        return _(
            "Il rapporto non nomina NESSUN codice, nemmeno di altri: e' il "
            "sintomo di una chiave letta col nome sbagliato, e allora "
            "riguarda TUTTI i pacchetti, non solo questo. Se Cdiscount usa "
            "un TERZO nome per quel campo, il risultato e' esattamente "
            "questo, per ogni pacchetto e per sempre, finche' nessuno guarda "
            "un rapporto vero.")

    def _manda_avviso(self, pacchetto, sommario, testo, termine, bandiera):
        """Un'attivita' sul pacchetto, e la bandiera SOLO se e' partita.

        ⚠️ **UNA SOLA DEFINIZIONE PER TUTTI GLI AVVISI DI QUESTO MODULO**, e
        non e' economia di righe: la regola «la bandiera si accende solo se
        l'avviso e' partito davvero» e' il difetto che si traveste da lavoro
        fatto, e due copie di quella regola divergono alla prima correzione.

        Rende tre verita' DISTINTE, e vanno lette tutte e tre:

        - `partito`: dopo questo passaggio un avviso ESISTE (o l'abbiamo
          creato adesso, o c'era gia');
        - `nuova`: l'abbiamo creato adesso — solo questo si conta;
        - `segnata`: la bandiera si e' scritta.

        ⚠️ **Il valore di `_al_riparo` si legge insieme a `partito`.**
        L'attivita' puo' essere stata creata e poi persa nello scarico
        all'USCITA dal savepoint — in Odoo la scrittura arriva al database
        li', non alla riga che l'ha chiamata. Fidarsi del solo «non ha
        sollevato» accenderebbe la bandiera su un avviso rotolato indietro, e
        quel pacchetto non avviserebbe MAI PIU'.

        ⚠️ **La bandiera sta in un savepoint SUO**, non in quello
        dell'attivita': se fallisce lei, l'attivita' gia' creata non deve
        tornare indietro con lei. Al giro dopo la ricerca per sommario la
        ritrova e non ne crea una seconda.
        """
        fatto = {"nuova": False, "partito": False, "segnata": False}

        def _crea():
            Attivita = self.env["mail.activity"].sudo()
            # ⚠️ L'ANTI-DOPPIONE GUARDA IL PACCHETTO **E** IL SOMMARIO, e il
            # `res_id` non e' ridondante: `uniq_canale_numero` e' un vincolo
            # PER CANALE, quindi due canali Cdiscount diversi possono avere
            # due pacchetti con LO STESSO numero — il modello lo dice a
            # chiare lettere («niente garantisce che Octopia non riusi la
            # stessa numerazione»). Senza `res_id`, l'attivita' del primo
            # canale zittirebbe quella del secondo, e per giunta gli
            # accenderebbe la bandiera: il secondo pacchetto non avviserebbe
            # mai.
            if Attivita.search_count(
                    [("res_model", "=", "cdiscount.pacchetto"),
                     ("res_id", "=", pacchetto.id),
                     ("summary", "=", sommario)]):
                fatto["nuova"] = False
            else:
                # ⚠️ A CHI. Lo stesso campo che questa casa usa gia' per gli
                # ordini non importati (`error_activity_user_id`,
                # integrations_core), col suo stesso ripiego. ⚠️ E il ripiego
                # e' debole di proposito solo qui: il cron gira come
                # `base.user_root`, quindi con il campo vuoto l'attivita'
                # finisce a OdooBot e non la vede nessuno. Il messaggio resta
                # comunque nel registro delle operazioni, che e' rosso; ma
                # quel campo va valorizzato sul canale, e chi legge questa
                # riga lo sappia.
                utente = self.channel.error_activity_user_id or self.env.user
                pacchetto.activity_schedule(
                    act_type_xmlid=ATTIVITA_TODO,
                    date_deadline=termine,
                    summary=sommario,
                    note=testo,
                    user_id=utente.id)
                fatto["nuova"] = True
            fatto["partito"] = True

        if not self._al_riparo(_crea):
            fatto["partito"] = False
            return fatto
        fatto["segnata"] = self._al_riparo(pacchetto.write, {bandiera: True})
        return fatto

    def sorveglia_scadenze(self):
        """Chiude gli scaduti e avvisa chi sta per scadere. Non tocca la rete.

        Rende `{"vivi": [...], "esito": {...}, "note": [...]}`: i pacchetti
        ancora buoni, i conti nella forma completa del giro, e cio' che c'e'
        da dire.

        ⚠️⚠️ **STA IN UN METODO PUBBLICO SUO, E NON E' ORDINE DEL CODICE: e'
        l'unica difesa contro un ROLLBACK ESTERNO.** Metterla dentro
        `raccogli()` e basta non serve a niente il giorno in cui `raccogli()`
        solleva — e basta un gettone che non arriva. Il cron avvolge la
        raccolta in un savepoint e cattura tutto: quel rollback si porta via
        anche i savepoint interni **gia' rilasciati**, cioe' le chiusure per
        scadenza e le attivita' gia' mandate. Con credenziali rotte per tre
        giorni, i pacchetti resterebbero `aperto`, nessuna attivita'
        comparirebbe, e si perderebbe tutto **in silenzio** — esattamente lo
        scenario che questo modulo esiste per rendere impossibile. Che «si
        rifanno al giro dopo» non consola: al giro dopo fallisce di nuovo.
        Percio' il cron la chiama **in un savepoint tutto suo**, prima della
        raccolta, e cio' che ha chiuso resta chiuso.

        ⚠️ **Non prende il turno**, ed e' voluto: e' idempotente per
        costruzione — un pacchetto gia' `scaduto` non e' piu' fra gli aperti,
        e le bandiere impediscono il secondo avviso — quindi due passaggi
        sovrapposti non fanno danno. Prenderlo qui farebbe sollevare una
        `UserError` a chi ha gia' il turno in mano (`raccogli`, un istante
        dopo), che e' proprio il chiamante piu' comune.

        ⚠️ **Non tocca la rete**, e questo e' cio' che le permette di stare
        prima: nessun gettone, nessuna chiamata, nessuna credenziale. Un
        canale configurato male chiude comunque i suoi scaduti.
        """
        Pacchetto = self.env["cdiscount.pacchetto"].sudo()
        aperti = Pacchetto.search(self._dominio_aperti())
        # ⚠️ La forma dell'esito e' COMPLETA anche qui, con gli zeri della
        # raccolta: e' la stessa che `_chiudi_raccolta` legge, e due forme
        # diverse per lo stesso giro sono il modo con cui una chiave sparisce
        # senza che nessuno se ne accorga.
        esito = {"pacchetti": 0, "chiusi": 0, "confermate": 0,
                 "rifiutate": 0, "mancanti": 0, "estranee": 0,
                 "in_lavorazione": 0, "incerti": 0, "illeggibili": 0,
                 "guasti": 0, "rimasti": 0, "scaduti": 0,
                 "senza_verdetto": 0, "avvisati": 0, "avvisi_falliti": 0,
                 "muti": 0}
        note = []
        # ⚠️ L'OROLOGIO SI LEGGE UNA VOLTA SOLA, qui. Due letture in due punti
        # diversi darebbero due «adesso» diversi, e la riga di confine (un
        # pacchetto che scade nell'istante fra le due) cadrebbe da una parte
        # per la chiusura e dall'altra per l'avviso: si avviserebbe di un
        # pacchetto appena chiuso.
        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        vivi = self._chiudi_gli_scaduti(aperti, adesso, esito, note)
        # ⚠️ L'avviso solo su cio' che e' ancora VIVO: un pacchetto appena
        # chiuso non «sta per scadere», e' gia' scaduto.
        self._avvisa_chi_scade(vivi, adesso, esito, note)
        return {"vivi": vivi, "esito": esito, "note": note}

    def raccogli(self):
        """Va a riprendere l'esito dei pacchetti aperti, uno per uno.

        L'ordine, e il primo punto e' l'unico che non si puo' spostare:

        1. ⚠️ **la scadenza, PRIMA di chiedere qualunque cosa alla rete**: i
           pacchetti oltre i tre giorni si chiudono, e chi sta per scadere
           avvisa una persona. Vedi `sorveglia_scadenze`, che e' pubblica
           apposta — il cron la chiama in un savepoint suo, cosi' una
           raccolta che fallisce non se la porta via;
        2. il gettone e il client, solo se resta qualcosa da interrogare;
        3. per ogni pacchetto vivo: `GET
           /products-integration-reports/<numero>`, poi `leggi_rapporto` e
           `riconcilia` — che sono gia' scritti, gia' provati e non si
           reimplementano qui.

        ⚠️ **Non solleva quando non c'e' niente da raccogliere**, ed e'
        l'opposto di `manda_schede`. Quella e' un bottone: «non c'e' niente
        da mandare» e' una notizia per chi ha appena cliccato. Questa la
        chiama un cron ogni mezz'ora, e una `UserError` diventerebbe una riga
        rossa nel registro ogni mezz'ora per tutto il tempo in cui non c'e'
        niente in volo — cioe' quasi sempre. Un registro che si riempie di
        rosso per una cosa che non e' un guasto e' peggio di un registro
        vuoto.
        """
        # ⚠️ Guardia 0 — il turno, come nell'invio: due raccolte sovrapposte
        # leggerebbero lo stesso pacchetto e scriverebbero due volte gli
        # stessi verdetti. La meccanica sta nella classe base.
        self._prendi_il_turno(_("raccolta degli esiti"))

        # ⚠️ LA SORVEGLIANZA PRIMA DI TUTTO, e si chiama ANCHE quando il cron
        # l'ha gia' chiamata un istante fa nel savepoint suo: il secondo
        # passaggio non trova piu' niente da chiudere ne' da avvisare (lo
        # stato e le bandiere lo dicono) e costa una `search`. In cambio,
        # nessun chiamante puo' perdere la sorveglianza dimenticandosi di
        # chiamarla — un bottone, una riga di comando, un cron riscritto
        # domani.
        guardia = self.sorveglia_scadenze()
        esito = guardia["esito"]
        note = guardia["note"]
        vivi = guardia["vivi"]

        if not vivi:
            if (esito["scaduti"] or esito["guasti"] or esito["avvisati"]
                    or esito["avvisi_falliti"]):
                # Niente da interrogare, ma qualcosa da DIRE: il giro lascia
                # la sua riga e non chiama nessuno.
                return self._chiudi_raccolta(esito, note, None)
            # Niente in volo: nessuna chiamata, nessun gettone, nessuna riga
            # di registro. Vedi la docstring.
            _logger.info(
                "Cdiscount sul canale %s: nessun pacchetto aperto, non c'e' "
                "niente da raccogliere.", self.channel.display_name)
            return esito

        client = self._client()
        # ⚠️ Il gettone si chiede ADESSO e solo se c'e' del lavoro: se le
        # credenziali sono sbagliate si deve leggere «il gettone non e'
        # arrivato», non un pacchetto illeggibile che manda a cercare il
        # guasto dalla parte di Cdiscount.
        self._gettone()

        fermata = None
        partenza = time.monotonic()
        for pacchetto in vivi:
            if esito["pacchetti"] >= MAX_PACCHETTI_PER_RACCOLTA:
                fermata = _(
                    "Il giro si e' fermato dopo %s pacchetti. I rimanenti si "
                    "raccolgono al giro dopo.") % MAX_PACCHETTI_PER_RACCOLTA
                break
            # ⚠️ Il tempo si guarda solo DOPO il primo pacchetto: altrimenti
            # un canale lento non raccoglierebbe mai niente. Stessa forma
            # dell'invio.
            if esito["pacchetti"] and (
                    time.monotonic() - partenza > SECONDI_PER_GIRO):
                fermata = _(
                    "Il giro si e' fermato da solo dopo %s secondi per non "
                    "farsi uccidere dal limite di tempo del worker. I "
                    "rimanenti si raccolgono al giro dopo.") % SECONDI_PER_GIRO
                break

            esito["pacchetti"] += 1
            # ⚠️ Il numero e la scadenza si leggono DENTRO il savepoint e si
            # tengono in due variabili Python. Sono letture SQL come le altre:
            # farle qui fuori vorrebbe dire che, su una transazione gia'
            # abortita, esploderebbero senza nessuna protezione e si
            # porterebbero via il giro intero. E siccome sono variabili, il
            # rollback non le tocca: il gestore d'errore qui sotto puo'
            # NOMINARE il pacchetto senza tornare a chiedere niente al
            # database — che e' la stessa eccezione che sta sopravvivendo.
            numero = ""
            scade = ""
            try:
                # ⚠️ IL SAVEPOINT PER PACCHETTO (regola 3). Un errore del
                # database dentro qui non lascia la transazione abortita, e
                # quel che il pacchetto aveva scritto a meta' torna indietro
                # tutto insieme: o si e' scritto il verdetto di tutte le sue
                # righe e il pacchetto e' chiuso, o non e' successo niente e
                # il giro dopo ci riprova. Uno stato a meta' sarebbe la cosa
                # peggiore — righe con un verdetto e un pacchetto ancora
                # aperto sono un rapporto che si rilegge, righe senza verdetto
                # e un pacchetto chiuso sono schede perse.
                with self.env.cr.savepoint():
                    numero = _testo(pacchetto.numero)
                    scade = pacchetto.scade_il
                    conti, riga, nota = self._raccogli_uno(pacchetto, numero,
                                                           client)
            except Exception as errore:  # noqa: BLE001
                # ⚠️ Un pacchetto che esplode non ferma gli altri, ed e' il
                # savepoint a renderlo vero: senza, da qui in poi ogni
                # istruzione fallirebbe.
                esito["guasti"] += 1
                nota = _(
                    "Pacchetto %(numero)s: la raccolta si e' interrotta "
                    "(%(tipo)s: %(errore)s). Niente e' stato scritto e il "
                    "pacchetto resta aperto: si riprova al giro dopo. "
                    "L'esito scade il %(scade)s.") % {
                        "numero": numero or _("(non letto)"),
                        "tipo": type(errore).__name__, "errore": errore,
                        "scade": self._ora_locale(scade)}
                _logger.exception(
                    "Cdiscount sul canale %s: raccolta interrotta sul "
                    "pacchetto %s.", self.channel.display_name,
                    numero or "numero non letto")
                note.append(nota)
                continue

            # ⚠️ SOLO ADESSO i conti entrano nell'esito (regola 3): un
            # contatore Python aggiornato dentro il savepoint sopravvivrebbe
            # al rollback e direbbe di aver raccolto cio' che e' tornato
            # indietro.
            for chiave, quanto in conti.items():
                esito[chiave] = esito.get(chiave, 0) + quanto
            if conti.get("muti"):
                # ⚠️ FUORI dal savepoint del pacchetto, come l'avviso di
                # scadenza: il pacchetto muto resta APERTO e le sue righe
                # `in_attesa` (regola 2), quindi non c'e' niente da cui
                # l'attivita' debba dipendere — e tutto da perdere se un
                # guasto successivo se la portasse indietro.
                self._avvisa_rapporto_muto(pacchetto, numero,
                                           conti.get("mancanti", 0),
                                           conti.get("estranee", 0), scade,
                                           esito, note)
            if nota:
                note.append(nota)
            if riga:
                # ⚠️ La riga di registro sta FUORI dal savepoint del
                # pacchetto, e nel suo: se scriverla fallisse, i verdetti
                # gia' scritti non devono tornare indietro con lei. E
                # `_al_riparo` rimette in piedi la transazione, cosi' il
                # pacchetto successivo parte da una transazione sana.
                self._al_riparo(self._registra, "cdiscount_raccogli_pacchetto",
                                riga[0], riga[1], None, numero)

        return self._chiudi_raccolta(esito, note, fermata)

    def _raccogli_uno(self, pacchetto, numero, client):
        """L'esito di UN pacchetto. Rende `(conti, riga_registro, nota)`.

        ⚠️ **Non tocca nessun contatore condiviso**: i suoi numeri li rende a
        chi chiama, che li somma solo se il savepoint e' uscito bene. Vedi la
        regola 3 in testa alla sezione.

        `riga_registro` e' `(esito, messaggio)` oppure `None`: si scrive una
        riga per pacchetto **solo quando un verdetto c'e' stato**. Un
        pacchetto ancora in lavorazione non lascia traccia — il cron gira ogni
        mezz'ora, e tre giorni di «sto ancora aspettando» sono 144 righe per
        pacchetto che insegnano a non leggere il registro.
        """
        Scheda = self.env["cdiscount.scheda"].sudo()
        # ⚠️ TUTTE le righe del pacchetto, non le sole `in_attesa`. «Quanti ne
        # sono partiti» e' il numero contro cui si misura il rapporto (regola
        # 2): filtrare per stato lo abbasserebbe, e un pacchetto dimezzato
        # tornerebbe verde perche' abbiamo dimenticato meta' di cio' che
        # avevamo mandato.
        righe = Scheda.search([("pacchetto_id", "=", pacchetto.id)])

        # ⚠️ Il numero va nel percorso ed e' ROBA CHE ARRIVA DA FUORI: e'
        # Cdiscount a sceglierlo, e `quote` lo mette al riparo dal giorno in
        # cui contiene una barra o uno spazio.
        risposta = client.chiama(
            "GET", "%s/%s" % (API_RAPPORTI, quote(numero, safe="")))

        incerta = self._causa_incerta(risposta)
        if incerta:
            # ⚠️ Non e' un verdetto: la rete e' caduta o il loro lato ha
            # risposto 5xx. Non si scrive niente, il pacchetto resta aperto e
            # si ripassa. A differenza dell'INVIO, qui un esito incerto non
            # e' pericoloso — una GET non crea niente la' fuori — e infatti
            # NON ci si ferma: gli altri pacchetti si raccolgono lo stesso.
            return ({"incerti": 1}, None, _(
                "Pacchetto %(numero)s: l'esito non si e' potuto leggere "
                "(%(perche)s). Resta aperto e si riprova; scade il "
                "%(scade)s.") % {"numero": numero, "perche": incerta,
                                 "scade": self._ora_locale(
                                     pacchetto.scade_il)})

        stato, esiti = leggi_rapporto(risposta.dati)

        if stato == IN_LAVORAZIONE:
            # ⚠️ REGOLA 1. Non e' un guasto: Cdiscount sta lavorando. Si
            # lascia aperto e NON SI SCRIVE NIENTE.
            return ({"in_lavorazione": 1}, None, _(
                "Pacchetto %(numero)s: Cdiscount ci sta ancora lavorando. "
                "Non e' un guasto; si ripassa. Scade il %(scade)s.")
                % {"numero": numero,
                   "scade": self._ora_locale(pacchetto.scade_il)})

        if stato != PRONTO:
            # «Non lo so»: un 404, un corpo che non e' JSON, uno stato che non
            # conosciamo, la chiave dei risultati che si chiama in un altro
            # modo. ⚠️ Nessun verdetto e nessuna scrittura — e il pacchetto
            # resta aperto, perche' e' esattamente la cosa che qualcuno deve
            # guardare prima che scada.
            if not risposta.ok:
                dettaglio = self._motivo_stato(risposta.stato,
                                               risposta.messaggio)
            else:
                dettaglio = ((risposta.testo or "")[:MAX_CORPO_NEL_MESSAGGIO]
                             or _("risposta vuota"))
            nota = _(
                "Pacchetto %(numero)s: il rapporto non si e' potuto leggere "
                "— %(dettaglio)s. Sulle sue %(quante)s schede non si scrive "
                "niente: resta aperto, e va guardato prima che scada il "
                "%(scade)s.") % {"numero": numero, "dettaglio": dettaglio,
                                 "quante": len(righe),
                                 "scade": self._ora_locale(
                                     pacchetto.scade_il)}
            _logger.warning("Cdiscount sul canale %s: %s",
                            self.channel.display_name, nota)
            return ({"illeggibili": 1}, None, nota)

        # ----------------------------------------------------------
        # Il rapporto e' PRONTO: si riconcilia.
        # ⚠️ I conti li fa `riconcilia`, che lavora sull'INSIEME dei codici
        # mandati e non sul numero delle voci lette. Non si ricontano qui: un
        # secondo conteggio scritto a mano e' il modo con cui questa famiglia
        # di lavori si e' gia' sbagliata due volte.
        # ----------------------------------------------------------
        mandati = [_testo(riga.codice) for riga in righe]
        conti = riconcilia(mandati, esiti)
        mancanti = set(conti["mancanti"])

        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        riuscite = []
        per_motivo = {}
        for riga in righe:
            codice = _codice(riga.codice)
            if codice in mancanti:
                # ⚠️ REGOLA 2: sulle mancanti NON SI SCRIVE NIENTE. Restano
                # `in_attesa` col loro pacchetto, che resta aperto: cosi' il
                # giro dopo le ripesca, e se non arriva mai un verdetto e' la
                # scadenza a dire «non lo so» — che e' la verita'. Nemmeno
                # `controllato_il`: una data su una riga senza verdetto si
                # legge «l'ho guardata e va bene».
                continue
            # ⚠️ SI INDICIZZA SENZA RETE, ed e' voluto. `riconcilia` ha
            # appena dichiarato che questo codice un verdetto ce l'ha: se non
            # c'e', le due funzioni si contraddicono — l'invariante su cui
            # questa pagina si regge e' rotto — e la cosa giusta e' ROMPERE.
            # Il savepoint del pacchetto riporta indietro tutto, non si scrive
            # nessun verdetto, il pacchetto resta aperto e il giro lo grida.
            # Un `.get()` con un `continue` sotto sarebbe la decisione della
            # regola 2 scritta una seconda volta: due copie della stessa
            # decisione sono il modo con cui una delle due smette di contare
            # senza che nessuno se ne accorga — e infatti, misurato, rendeva
            # invisibile la mutazione che toglieva la prima.
            verdetto = esiti[codice]
            if verdetto["esito"] == RIUSCITO:
                riuscite.append(riga.id)
            else:
                # ⚠️ Raggruppate per MOTIVO: un pacchetto porta fino a 10.000
                # righe, e una `write` per riga sarebbero 10.000 UPDATE. I
                # motivi distinti sono pochi (e' lo stesso campo sbagliato su
                # tante schede), quindi il raggruppamento costa una manciata
                # di scritture invece di diecimila.
                per_motivo.setdefault(_testo(verdetto["motivo"]),
                                      []).append(riga.id)
        if riuscite:
            # ⚠️ Il motivo vecchio si toglie: una scheda riuscita che tiene
            # addosso il motivo di un rifiuto precedente si legge come
            # rifiutata di nuovo.
            Scheda.browse(riuscite).write({"stato": RIUSCITO,
                                           "motivo": False,
                                           "controllato_il": adesso})
        for motivo, ids in per_motivo.items():
            Scheda.browse(ids).write({"stato": RIFIUTATO,
                                      "motivo": motivo or False,
                                      "controllato_il": adesso})

        chiuso = not conti["mancanti"]
        if chiuso:
            # ⚠️ Si chiude SOLO quando il rapporto ha nominato tutti. Vedi la
            # regola 2: chiudere con delle mancanti le lascerebbe senza
            # verdetto per sempre.
            pacchetto.write({"stato": RACCOLTO})

        numeri = {"confermate": conti["confermati"],
                  "rifiutate": conti["rifiutati"],
                  "mancanti": len(conti["mancanti"]),
                  "estranee": len(conti["estranee"])}
        if chiuso:
            numeri["chiusi"] = 1
        # ⚠️ IL RAPPORTO MUTO: PRONTO, e non nomina NEMMENO UNA delle nostre.
        # Non e' un rapporto a meta' — e' il sospetto che il campo con cui
        # riconosciamo una scheda si chiami (o si scriva) in un altro modo, e
        # allora sara' cosi' su OGNI pacchetto, per sempre. Qui si CONTA e
        # basta: l'avviso lo manda `raccogli()`, fuori dal savepoint di questo
        # pacchetto. Vedi `_avvisa_rapporto_muto`.
        #
        # ⚠️ **E il criterio guarda LE VOCI CHE NOMINANO I MANDATI, non le
        # voci totali.** `confermati` e `rifiutati` sono esattamente quelle:
        # le estranee NON entrano nel conto, e non devono entrarci in
        # nessuno dei due versi. Non per far scattare l'avviso (un rapporto di
        # sole estranee e' proprio il caso in cui il codice torna
        # TRASFORMATO, che e' un difetto di chiave a tutti gli effetti), e
        # nemmeno per spegnerlo. Cambia solo COSA SI DICE: vedi
        # `_riga_rapporto` e `_avvisa_rapporto_muto`, che si biforcano sulle
        # estranee perche' le due strade portano a guardare cose diverse.
        if conti["mancanti"] and not (conti["confermati"]
                                      or conti["rifiutati"]):
            numeri["muti"] = 1
        return (numeri,
                self._riga_rapporto(pacchetto, numero, righe, conti, chiuso,
                                    risposta),
                None)

    def _riga_rapporto(self, pacchetto, numero, righe, conti, chiuso,
                       risposta):
        """La riga di registro di un pacchetto letto: `(esito, messaggio)`.

        ⚠️ **Verde solo se non manca niente, non avanza niente e nessuna
        scheda e' stata rifiutata.** E' la stessa regola della notifica
        dell'invio, e sta qui perche' e' qui che si decide: chi legge il verde
        non apre il messaggio, e una mancante e' precisamente la cosa che va
        guardata entro tre giorni.
        """
        # ⚠️ QUANTE NE SONO PARTITE si prende dai conti di `riconcilia`, non
        # da `len(righe)`, e la differenza non e' teorica: `riconcilia`
        # deduplica i codici NORMALIZZATI, perche' `corpo_scheda` normalizza
        # prima di spedire e a Cdiscount ne e' arrivata una sola. Due righe
        # Odoo con «HDC1» e «HDC1 » — che il vincolo di unicita' non impedisce,
        # sono stringhe grezze diverse — davano «partite 2, confermate 1»
        # pur avendo scritto il verdetto su tutte e due: nessuna scheda persa,
        # ma un conto sbagliato a video. Vale l'invariante dichiarata nella
        # docstring di `riconcilia`.
        partite = (conti["confermati"] + conti["rifiutati"]
                   + len(conti["mancanti"]))
        # ⚠️ E QUANTE VOCI HA PORTATO IL RAPPORTO si scrive SEMPRE, anche
        # quando tutto torna. E' una riga che non cambia niente e vale la
        # prima lettura vera: la firma della paginazione e' un numero TONDO —
        # 100, 500, 1.000 — al posto delle diecimila partite, e scritta la si
        # riconosce a occhio invece di doversi ricordare di contarla.
        #
        # ⚠️ **«HA PORTATO», non «ne ha nominate».** Questo conto comprende le
        # voci ESTRANEE — deve comprenderle, o non riconoscerebbe la
        # paginazione — quindi non e' il numero delle NOSTRE schede nominate.
        # Chiamarlo cosi' faceva contraddire il messaggio con se stesso: «il
        # rapporto ne ha nominate 1» seguito da «NON NOMINA NEMMENO UNA».
        voci = (conti["confermati"] + conti["rifiutati"]
                + len(conti["estranee"]))
        pezzi = [_(
            "Pacchetto %(numero)s: partite %(partite)s schede, il rapporto ha "
            "portato %(voci)s voci — confermate %(confermate)s, rifiutate "
            "%(rifiutate)s, senza verdetto %(mancanti)s.")
            % {"numero": numero, "partite": partite, "voci": voci,
               "confermate": conti["confermati"],
               "rifiutate": conti["rifiutati"],
               "mancanti": len(conti["mancanti"])}]
        if len(righe) != partite:
            pezzi.append(_(
                "⚠️ In Odoo le righe di questo pacchetto sono %(righe)s ma i "
                "codici distinti sono %(codici)s: due righe portano lo stesso "
                "codice a meno degli spazi, e a Cdiscount ne e' arrivato uno. "
                "Il verdetto e' stato scritto su tutte, ma vanno unite.")
                % {"righe": len(righe), "codici": partite})
        if not righe:
            # ⚠️ Un pacchetto senza nemmeno una riga collegata NON e' un
            # successo a zero: vuol dire che l'invio non e' riuscito ad
            # attaccargli le schede (succede, ed e' un ramo dichiarato di
            # `manda_schede`). Il rapporto e' stato letto e il pacchetto si
            # chiude — non c'e' piu' niente da chiedergli — ma la riga e'
            # rossa, perche' quelle schede sono da qualche parte senza
            # verdetto.
            pezzi.append(_(
                "⚠️ In Odoo questo pacchetto non ha nessuna scheda "
                "collegata: l'esito e' stato letto e non si e' potuto "
                "attribuire a nessuno."))
        if conti["mancanti"]:
            pezzi.append(_(
                "⚠️ Il rapporto NON nomina %(quante)s schede che erano "
                "partite: %(elenco)s. Restano «in attesa» e il pacchetto "
                "resta aperto — si riprova al giro dopo, e se l'esito non "
                "arriva scade il %(scade)s.")
                % {"quante": len(conti["mancanti"]),
                   "elenco": _elenco_corto(conti["mancanti"]),
                   "scade": self._ora_locale(pacchetto.scade_il)})
            # ⚠️ IL SOSPETTO DELLA PAGINAZIONE, e solo qui: un rapporto
            # completo non ha niente da sospettare. Non cambia nessuna
            # decisione — il pacchetto resta aperto comunque — ma separa le
            # due diagnosi che oggi producono la stessa riga: «Cdiscount non
            # ha detto niente di queste schede» e «le ha dette in una pagina
            # che non abbiamo chiesto». Vedi `CHIAVI_PAGINAZIONE`.
            if not (conti["confermati"] or conti["rifiutati"]):
                # ⚠️ ED E' UNA DIAGNOSI DIVERSA, non un parziale piu' grande.
                # Un rapporto che nomina ALCUNE delle nostre e' una notizia su
                # questo pacchetto; uno che non ne nomina NESSUNA e' una
                # notizia su tutti. Le due righe non si confondono a colpo
                # d'occhio apposta.
                pezzi.append(_(
                    "⚠️⚠️ IL RAPPORTO NON NOMINA NEMMENO UNA delle schede "
                    "partite, ed e' arrivato COMPLETO: non e' un rapporto a "
                    "meta'. Il riferimento con cui riconosciamo una scheda si "
                    "cerca sotto «%(chiavi)s», due nomi presi dalla "
                    "documentazione e mai visti sul vero. Ne e' aperta "
                    "un'attivita' a una persona; se non fosse partita, la "
                    "riga finale del giro lo dice.") % {
                        "chiavi": "», «".join(CHIAVI_CODICE)})
                # ⚠️ E QUI LE DUE STRADE SI SEPARANO, perche' portano a
                # guardare cose diverse. Un rapporto che non nomina NESSUNO —
                # ne' i nostri ne' altri — punta al NOME della chiave, ed e'
                # un difetto che riguarderebbe ogni pacchetto. Un rapporto
                # che nomina solo codici che non riconosciamo ha parlato: o il
                # valore torna trasformato (e allora e' di nuovo un difetto di
                # ogni pacchetto), oppure quel rapporto non e' di questo
                # pacchetto. Dire «riguarda TUTTI i pacchetti» in tutti e due
                # i casi e' un'affermazione che il modulo non puo' sostenere.
                if conti["estranee"]:
                    pezzi.append(_(
                        "Il rapporto pero' NOMINA %(quante)s codici, che non "
                        "riconosciamo: puo' essere una CHIAVE SBAGLIATA nel "
                        "VALORE — il riferimento ci torna trasformato, con un "
                        "prefisso, tagliato o rinumerato, e allora sara' "
                        "cosi' su OGNI pacchetto — oppure questo rapporto non "
                        "e' di questo pacchetto. Si distinguono guardando i "
                        "codici elencati qui sotto: se assomigliano ai nostri "
                        "e' la prima, se sono di altri prodotti e' la "
                        "seconda.") % {"quante": len(conti["estranee"])})
                else:
                    pezzi.append(_(
                        "E il rapporto non nomina NESSUN codice, nemmeno di "
                        "altri: e' una CHIAVE SBAGLIATA nel NOME, cioe' il "
                        "sospetto che Cdiscount usi un TERZO nome per quel "
                        "campo. Se e' cosi', ogni pacchetto tornera' come "
                        "questo e nessuna scheda avra' mai un verdetto."))
            pagine = _chiavi_di_paginazione(risposta)
            if pagine:
                pezzi.append(_(
                    "⚠️ Il corpo del rapporto porta %(chiavi)s: potrebbe "
                    "essere A PAGINE e non incompleto, e questo modulo ne "
                    "legge una sola. Va guardato prima di dare per perse le "
                    "schede qui sopra.") % {"chiavi": ", ".join(pagine)})
        if conti["estranee"]:
            pezzi.append(_(
                "⚠️ Il rapporto nomina %(quante)s codici che in questo "
                "pacchetto non c'erano: %(elenco)s. O si sta leggendo il "
                "rapporto di un altro pacchetto, o i codici non "
                "corrispondono piu': i numeri qui sopra non vogliono dire "
                "quello che sembrano.")
                % {"quante": len(conti["estranee"]),
                   "elenco": _elenco_corto(conti["estranee"])})
            # ⚠️ E LA CAUSA DA GUARDARE PER PRIMA E' IN CASA, non da
            # Cdiscount: una scheda uscita da questo pacchetto per rientrare
            # in uno nuovo compare qui come estranea. E' il difetto che
            # `_candidate` chiude tenendo ferme le rifiutate finche' il loro
            # pacchetto e' ancora aperto: se questa riga ricompare, il primo
            # posto da guardare e' quello.
            pezzi.append(_(
                "Prima di cercare il guasto da Cdiscount: una scheda uscita "
                "da questo pacchetto per rientrare in uno nuovo comparirebbe "
                "esattamente cosi'. Il modulo non lo fa piu' (una scheda "
                "rifiutata aspetta che il suo pacchetto sia chiuso), ma se "
                "uno di questi codici e' nostro e sta in un altro pacchetto, "
                "e' quella la spiegazione."))
        if chiuso:
            pezzi.append(_("Il pacchetto e' chiuso: non si chiede piu'."))
        pulito = not (conti["mancanti"] or conti["estranee"]
                      or conti["rifiutati"] or not righe)
        return ("success" if pulito else "error", "\n".join(pezzi))

    def _chiudi_raccolta(self, esito, note, fermata):
        """Tira le somme del giro e lascia una riga nel registro.

        ⚠️ Dentro il proprio savepoint, come `_chiudi`: se i conti o il
        registro esplodono, i verdetti gia' scritti restano scritti e i
        pacchetti chiusi restano chiusi. L'esito grezzo finisce nel registro
        di sistema, che un rollback non tocca.

        ⚠️ **E un giro in cui c'era solo da aspettare non lascia riga.** Il
        cron gira ogni mezz'ora e la finestra e' di tre giorni: un pacchetto
        in lavorazione produrrebbe 144 righe verdi identiche, e 144 righe
        verdi sono il modo migliore di far passare inosservata la
        centoquarantacinquesima, che e' rossa.
        """
        esito["fermata"] = fermata or False
        try:
            with self.env.cr.savepoint():
                Pacchetto = self.env["cdiscount.pacchetto"].sudo()
                esito["rimasti"] = Pacchetto.search_count(
                    self._dominio_aperti())
                # ⚠️ `scaduti` entra nel verde, e non e' un contatore in piu':
                # un pacchetto scaduto e' l'esito di fino a 10.000 schede
                # perso per sempre. E' LA cosa che non deve mai passare per
                # un giro andato bene.
                verde = not (esito["mancanti"] or esito["estranee"]
                             or esito["rifiutate"] or esito["incerti"]
                             or esito["illeggibili"] or esito["guasti"]
                             or esito["scaduti"]
                             or esito["avvisi_falliti"] or fermata)
                guardati = esito["pacchetti"]
                # ⚠️ «C'era solo da aspettare» NON vale se qualcosa e'
                # scaduto o se e' partito un avviso: quelle due notizie sono
                # esattamente cio' che non deve restare senza riga. Un
                # pacchetto chiuso per scadenza non e' fra i `guardati` (non
                # lo si e' interrogato), quindi senza questa condizione un
                # giro con «uno scaduto e uno in lavorazione» tornerebbe
                # muto — e il pacchetto perso non lo saprebbe nessuno.
                solo_attesa = (guardati
                               and esito["in_lavorazione"] == guardati
                               and not fermata
                               and not (esito["scaduti"] or esito["avvisati"]
                                        or esito["avvisi_falliti"]
                                        or esito["guasti"]))
                if solo_attesa:
                    _logger.info(
                        "Cdiscount sul canale %s: %s pacchetti ancora in "
                        "lavorazione da Cdiscount, niente da scrivere.",
                        self.channel.display_name, esito["in_lavorazione"])
                    return esito
                pezzi = [
                    _("Pacchetti guardati: %(guardati)s, chiusi "
                      "%(chiusi)s, ancora aperti %(rimasti)s.")
                    % {"guardati": esito["pacchetti"],
                       "chiusi": esito["chiusi"],
                       "rimasti": esito["rimasti"]},
                    _("Schede confermate: %s.") % esito["confermate"],
                    _("Rifiutate da Cdiscount: %s.") % esito["rifiutate"],
                    _("Partite e mai nominate dal rapporto: %s.")
                    % esito["mancanti"],
                    _("Voci estranee nei rapporti: %s.") % esito["estranee"],
                    _("Pacchetti ancora in lavorazione: %(attesa)s, non "
                      "raggiungibili %(incerti)s, illeggibili "
                      "%(illeggibili)s, interrotti da un guasto "
                      "%(guasti)s.")
                    % {"attesa": esito["in_lavorazione"],
                       "incerti": esito["incerti"],
                       "illeggibili": esito["illeggibili"],
                       "guasti": esito["guasti"]},
                    # ⚠️ SI SCRIVE SEMPRE, anche a zero. Uno zero letto ogni
                    # giorno e' cio' che rende visibile il primo uno: un
                    # conto che compare solo quando e' diverso da zero non si
                    # riconosce a colpo d'occhio, si legge.
                    _("⚠️ Pacchetti SCADUTI e chiusi in questo giro: "
                      "%(scaduti)s, e le schede rimaste senza verdetto sono "
                      "%(schede)s. Rapporti che non nominano nessuna scheda: "
                      "%(muti)s. Avvisi mandati a una persona: %(avvisi)s, "
                      "non partiti: %(falliti)s.")
                    % {"scaduti": esito["scaduti"],
                       "schede": esito["senza_verdetto"],
                       "muti": esito.get("muti", 0),
                       "avvisi": esito["avvisati"],
                       "falliti": esito["avvisi_falliti"]},
                ]
                if fermata:
                    pezzi.append(fermata)
                pezzi.extend(note)
                self._registra("cdiscount_raccogli",
                               "success" if verde else "error",
                               "\n".join(pezzi))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Cdiscount sul canale %s: la chiusura della raccolta e' "
                "fallita. Esito grezzo: %s", self.channel.display_name, esito)
            esito.setdefault("rimasti", 0)
            esito["chiusura_fallita"] = 1
        return esito

    # ------------------------------------------------------------------
    # Il contratto della classe base
    # ------------------------------------------------------------------
    def pull_orders(self):
        """Gli ordini sono la Consegna 3: qui non c'e' niente da scaricare.

        ⚠️ NON solleva `NotImplementedError`, ed e' tutto il punto di questo
        metodo. `cron_pull_all_channels` (integrations_core) scorre TUTTI i
        canali attivi e chiama `pull_orders()`: un'eccezione qui diventerebbe
        una riga `pull_orders / error` nel registro delle operazioni A OGNI
        PASSAGGIO. Un registro che si riempie di rosso per una cosa che non e'
        un guasto e' peggio di un registro vuoto: si impara a non leggerlo, e
        il rosso vero — un pacchetto di esito ignoto — passa in mezzo agli
        altri senza che nessuno lo veda. Stessa scelta di Kaufland.
        """
        _logger.info(
            "Cdiscount sul canale %s: gli ordini sono la Consegna 3, non "
            "c'e' niente da scaricare.", self.channel.display_name)
        return True
