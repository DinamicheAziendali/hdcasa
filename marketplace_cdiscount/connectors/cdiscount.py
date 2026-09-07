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
    IDENTICA,
    IN_LAVORAZIONE,
    MODIFICA,
    PRONTO,
    RIFIUTATO,
    RIUSCITO,
    _codice,
    _e_scalare,
    leggi_rapporto,
    riconcilia,
)
from .cdiscount_ordini import (
    CORRIERI,
    MARCHI_CORRIERI,
    MAX_PAGINE_ORDINI,
    ORDINI_PER_PAGINA,
    PERCORSO_CONTEGGIO_IN_ATTESA,
    corpo_spedizione,
    etichetta_corriere,
    leggi_ordine,
    percorso_ordini,
    spedizione_righe,
    totale_righe,
    url_tracciamento,
)
from .cdiscount_offerte import (
    MAX_PER_LOTTO,
    RIFIUTATO_IN_BLOCCO,
    SOGLIA_KG,
    corpo_offerta,
    cursore_da_link,
    leggi_esiti_offerte,
    lotti,
    modo_consegna,
    motivo_pacchetto,
    numero_pacchetto_offerte,
    regge_oltre_30kg,
    stato_pacchetto,
)
from .cdiscount_prodotti import (
    LetturaInterrotta,
    leggi_prodotto,
    pagine_prodotti,
    secchi_tornano,
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
from ..models.cdiscount_offerta import DA_MANDARE, RITIRATA
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
    TIPO_OFFERTE,
    TIPO_SCHEDE,
)

_logger = logging.getLogger(__name__)

# Le due chiamate di questo compito. La seconda non si fa da qui: si NOMINA
# nei messaggi, perche' e' quella con cui una persona va a vedere cosa c'e'
# davvero la' fuori quando un esito e' ignoto.
API_SCHEDE = "/products-integration"
API_SCHEDE_NOSTRE = "/products"
# ⚠️ MISURATO il 2026-09-02: `GET /categories/{codice}` risponde un oggetto
# nudo con `label`, `level` (1, 2 o 3), `parentReference`. E' cio' che
# permette di verificare il LIVELLO della categoria prima di spedire — il
# controllo che la prima versione dichiarava impossibile.
API_CATEGORIE = "/categories"
LIVELLO_CATEGORIA = 3
# CONSEGNA 2 — le offerte. Il ciclo a quattro chiamate (LETTO) e i modi di
# consegna (MISURATO). Vedi docs/cdiscount-offerte-consegna-2.md.
API_OFFER_PACKAGES = "/offer-packages"
API_MODI_CONSEGNA = "/sellers/delivery-modes"
# Un giro manda al massimo dieci lotti: e' il tetto del tempo del worker,
# non di Cdiscount (che ne prende 50.000 per pacchetto).
MAX_OFFERTE_PER_GIRO = MAX_PER_LOTTO * 10
MAX_PAGINE_ESITI_OFFERTE = 500
OPERAZIONE_ALLINEA = "cdiscount_allinea_offerte"
# ⚠️ Il rapporto di UN pacchetto. **MISURATO il 2026-09-02** sull'account
# vero (`docs/cdiscount-misurato-2026-09-02.md`): il numero va in QUERY,
# `?packageId=<numero>`, e il rapporto e' PAGINATO con `pageIndex`/`pageSize`.
# La prima versione lo metteva nel percorso (`/<numero>`), che sul vero
# risponde il 404 generico del gateway — e ogni esito sarebbe rimasto
# «sconosciuto» fino a scadere.
#
# ⚠️ Il timore della prima versione — «un parametro sbagliato potrebbe far
# rispondere i rapporti di TUTTI i pacchetti» — era FONDATO: senza
# `packageId` l'endpoint risponde 200 con l'elenco di tutti. Il parametro si
# manda sempre, `riconcilia` lavora sull'insieme dei codici mandati, e le
# voci estranee si dicono: sono le tre guardie contro il rapporto di un altro.
API_RAPPORTI = "/products-integration-reports"
# Pagine da 100 (misurato: `pageSize=100` accettato). Un pacchetto pieno fa
# 10.000 righe, cioe' 100 pagine: il tetto copre esattamente quello, e oltre
# non si indovina.
RIGHE_PER_PAGINA = 100
MAX_PAGINE_RAPPORTO = MAX_PER_PACCHETTO // RIGHE_PER_PAGINA
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


@register_connector("cdiscount", "Cdiscount (Octopia)")
class CdiscountConnector(MarketplaceConnector):

    # ⚠️ Cosa Cdiscount NON usa della scheda del canale: le credenziali sono
    # OAuth2 (identificativo e segreto cliente) nel suo tab, e le schede si
    # mandano a pacchetti via API, non a feed CSV.
    usa_api_key = False
    usa_ambienti = False
    usa_feed_csv = False
    usa_immagini_feed = False
    usa_mappa_catalogo = False
    usa_presa_in_carico = False   # non esiste, su questo marketplace

    # CONSEGNA 3 — i corrieri di Octopia (66, MISURATI) e la traduzione dei
    # marchi del tronco: in Francia solo BRT e GLS. Vedi cdiscount_ordini.
    carrier_codes = CORRIERI
    carrier_brand_codes = MARCHI_CORRIERI

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



    @staticmethod
    def _categoria_in_chiaro(esito):
        """Il codice della categoria e, se letta, la sua etichetta."""
        nome = _testo(esito.get("categoria_nome"))
        codice = _testo(esito.get("categoria")) or "?"
        return "%s «%s»" % (codice, nome) if nome else codice

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

    def _crea_pacchetto(self, numero, adesso, scade, tipo=TIPO_SCHEDE,
                        pronto=True):
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
            "tipo": tipo,
            "pronto": pronto,
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
                       "categoria": self._categoria_in_chiaro(esito)},
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
        # ⚠️ Dal 2026-09-02 (Consegna 2) i pacchetti sono di DUE tipi, e
        # il raccoglitore li guarda tutti e due: e' `_raccogli_uno` a
        # biforcarsi sul tipo, perche' l'esito si chiede a due indirizzi
        # diversi. La scadenza invece e' la stessa per tutti.
        return [("channel_id", "=", self.channel.id),
                ("tipo", "in", (TIPO_SCHEDE, TIPO_OFFERTE)),
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
        if _testo(pacchetto.tipo) == TIPO_OFFERTE:
            Offerta = self.env["cdiscount.offerta"].sudo()
            righe = Offerta.search([("pacchetto_id", "=", pacchetto.id),
                                    ("stato", "=", IN_ATTESA)])
            if righe:
                righe.write({
                    "stato": SCONOSCIUTO_SCHEDA,
                    "motivo": _(
                        "Il pacchetto di offerte %(numero)s e' scaduto il "
                        "%(scade)s senza un esito: NON sappiamo se il prezzo "
                        "e la giacenza mandati siano arrivati. Il prossimo "
                        "allineamento li rimanda (un aggiornamento ripetuto "
                        "non fa danni).")
                    % {"numero": numero, "scade": self._ora_locale(scade)},
                })
            return len(righe)
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
              "cerca sotto due nomi, «%(chiavi)s»: il primo e' quello "
              "misurato sul vero il 2026-09-02, il secondo quello della "
              "prima documentazione.\n\n"
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
                 "muti": 0, "attese": 0}
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
           /products-integration-reports?packageId=<numero>`, pagina per
           pagina finche' non ne arriva una a meta', poi `leggi_rapporto`
           e `riconcilia` sul rapporto INTERO — che sono gia' scritti, gia'
           provati e non si reimplementano qui. Il verdetto si scrive solo
           quando TUTTE le pagine sono arrivate.

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
        if _testo(pacchetto.tipo) == TIPO_OFFERTE:
            # CONSEGNA 2: l'esito di un pacchetto di OFFERTE si chiede a un
            # altro indirizzo e ha un'altra forma. Stesse regole.
            return self._raccogli_offerte(pacchetto, numero, client)

        Scheda = self.env["cdiscount.scheda"].sudo()
        # ⚠️ TUTTE le righe del pacchetto, non le sole `in_attesa`. «Quanti ne
        # sono partiti» e' il numero contro cui si misura il rapporto (regola
        # 2): filtrare per stato lo abbasserebbe, e un pacchetto dimezzato
        # tornerebbe verde perche' abbiamo dimenticato meta' di cio' che
        # avevamo mandato.
        righe = Scheda.search([("pacchetto_id", "=", pacchetto.id)])

        # ⚠️ IL RAPPORTO SI LEGGE TUTTO, PAGINA PER PAGINA, PRIMA DI SCRIVERE
        # QUALUNQUE VERDETTO. Una pagina piena chiede la successiva; una
        # successiva che non arriva rende incerto il pacchetto INTERO.
        # Scrivere la prima pagina e aspettare la seconda lascerebbe righe
        # con un verdetto e un pacchetto aperto — un rapporto che si rilegge,
        # e le righe gia' giudicate che tornano come «estranee».
        #
        # ⚠️ Il numero e' ROBA CHE ARRIVA DA FUORI: e' Cdiscount a sceglierlo,
        # e `urlencode` lo mette al riparo dal giorno in cui contiene un
        # carattere che in una query non puo' stare.
        righe_rapporto = []
        pagine = 0
        risposta = None
        completo = False
        while True:
            if pagine >= MAX_PAGINE_RAPPORTO:
                # ⚠️ Oltre le 100 pagine il rapporto dice piu' righe di
                # quante un pacchetto possa portarne: non si indovina, non
                # si scrive niente, si dice.
                return ({"incerti": 1}, None, _(
                    "Pacchetto %(numero)s: il rapporto supera %(pagine)s "
                    "pagine da %(righe)s, cioe' piu' righe di quante un "
                    "pacchetto possa portarne. Non si scrive niente e il "
                    "pacchetto resta aperto: va guardato prima che scada il "
                    "%(scade)s.") % {"numero": numero,
                                     "pagine": MAX_PAGINE_RAPPORTO,
                                     "righe": RIGHE_PER_PAGINA,
                                     "scade": self._ora_locale(
                                         pacchetto.scade_il)})
            pagine += 1
            risposta = client.chiama(
                "GET", "%s?%s" % (API_RAPPORTI, urlencode(
                    {"packageId": numero, "pageIndex": pagine,
                     "pageSize": RIGHE_PER_PAGINA})))

            incerta = (self._causa_incerta(risposta)
                       or self._quota_esaurita(risposta))
            if incerta:
                # ⚠️ Non e' un verdetto: la rete e' caduta, il loro lato ha
                # risposto 5xx, o la quota oraria e' finita. Non si scrive
                # niente — NEMMENO le pagine gia' lette — il pacchetto resta
                # aperto e si ripassa. A differenza dell'INVIO, qui un esito
                # incerto non e' pericoloso (una GET non crea niente la'
                # fuori) e infatti NON ci si ferma: gli altri pacchetti si
                # raccolgono lo stesso.
                dove = ""
                if pagine > 1:
                    dove = _(" alla pagina %s") % pagine
                return ({"incerti": 1}, None, _(
                    "Pacchetto %(numero)s: l'esito non si e' potuto leggere"
                    "%(dove)s (%(perche)s). Resta aperto e si riprova; scade "
                    "il %(scade)s.") % {"numero": numero, "dove": dove,
                                        "perche": incerta,
                                        "scade": self._ora_locale(
                                            pacchetto.scade_il)})
            if not risposta.ok:
                break
            pagina = risposta.dati
            if not isinstance(pagina, list):
                # Un corpo che non porta `items` come elenco: illeggibile,
                # e lo si dice sotto con il corpo com'e' arrivato.
                break
            righe_rapporto.extend(pagina)
            if len(pagina) < RIGHE_PER_PAGINA:
                completo = True
                break

        if completo:
            stato, esiti = leggi_rapporto(righe_rapporto)
        else:
            # ⚠️ Si passa il corpo GREZZO dell'ultima risposta, non le righe
            # unite: `leggi_rapporto` lo legge «sconosciuto» (non e' un
            # elenco), e il ramo sotto scrive il dettaglio che aiuta.
            stato, esiti = leggi_rapporto(
                risposta.dati if risposta.ok else None)

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

        # ⚠️ TUTTE VALIDATE E NIENT'ALTRO: Cdiscount ha accettato le schede e
        # le sta ancora mettendo sul sito. E' «in lavorazione» a tutti gli
        # effetti — niente scritto, niente registro, si ripassa — e NON e' un
        # rapporto muto: ci ha nominate tutte. Le attese stanno gia' dentro
        # le mancanti (vedi `riconcilia`), quindi il confronto e' esatto.
        if (conti["attese"]
                and not (conti["confermati"] or conti["rifiutati"]
                         or conti["estranee"])
                and len(conti["attese"]) == len(conti["mancanti"])):
            return ({"in_lavorazione": 1, "attese": len(conti["attese"])},
                    None, _(
                "Pacchetto %(numero)s: Cdiscount ha validato le sue "
                "%(quante)s schede e le sta ancora integrando. Non e' un "
                "guasto; si ripassa. Scade il %(scade)s.")
                % {"numero": numero, "quante": len(conti["attese"]),
                   "scade": self._ora_locale(pacchetto.scade_il)})

        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        # ⚠️ Raggruppate per OPERAZIONE (e per motivo, sulle rifiutate): un
        # pacchetto porta fino a 10.000 righe e una `write` per riga sarebbero
        # 10.000 UPDATE. Le operazioni sono tre e i motivi distinti pochi.
        riuscite = {}
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
            # ⚠️ L'OPERAZIONE SI CONSERVA: e' la risposta a «esisteva gia'?»
            # che arriva solo col rapporto, e col rapporto sparirebbe. Una
            # che non conosciamo resta vuota, e non cambia il verdetto.
            operazione = _testo(verdetto.get("operazione")) or False
            if verdetto["esito"] == RIUSCITO:
                riuscite.setdefault(operazione, []).append(riga.id)
            else:
                per_motivo.setdefault(
                    (_testo(verdetto["motivo"]), operazione),
                    []).append(riga.id)
        esistevano = 0
        for operazione, ids in riuscite.items():
            # ⚠️ Il motivo vecchio si toglie: una scheda riuscita che tiene
            # addosso il motivo di un rifiuto precedente si legge come
            # rifiutata di nuovo.
            Scheda.browse(ids).write({"stato": RIUSCITO,
                                      "motivo": False,
                                      "controllato_il": adesso,
                                      "operazione": operazione})
            if operazione in (IDENTICA, MODIFICA):
                esistevano += len(ids)
        for (motivo, operazione), ids in per_motivo.items():
            Scheda.browse(ids).write({"stato": RIFIUTATO,
                                      "motivo": motivo or False,
                                      "controllato_il": adesso,
                                      "operazione": operazione})

        chiuso = not conti["mancanti"]
        if chiuso:
            # ⚠️ Si chiude SOLO quando il rapporto ha nominato tutti. Vedi la
            # regola 2: chiudere con delle mancanti le lascerebbe senza
            # verdetto per sempre.
            pacchetto.write({"stato": RACCOLTO})

        numeri = {"confermate": conti["confermati"],
                  "rifiutate": conti["rifiutati"],
                  "mancanti": len(conti["mancanti"]),
                  "estranee": len(conti["estranee"]),
                  "attese": len(conti["attese"])}
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
        # ⚠️ E UNA SCHEDA VALIDATA E' STATA NOMINATA: un rapporto di sole
        # attese piu' qualche mancante vera non e' muto, sta lavorando.
        if conti["mancanti"] and not (conti["confermati"]
                                      or conti["rifiutati"]
                                      or conti["attese"]):
            numeri["muti"] = 1
        return (numeri,
                self._riga_rapporto(pacchetto, numero, righe, conti, chiuso,
                                    pagine, esistevano),
                None)

    @staticmethod
    def _quota_esaurita(risposta):
        """Il 403 della quota oraria, che e' «riprova», non un verdetto.

        ⚠️ MISURATO il 2026-09-02, e non documentato: dopo circa 600
        chiamate in pochi minuti Octopia risponde 403 con `{"statusCode":
        403, "message": "Out of call volume quota. Quota will be replenished
        in 00:11:35."}`. Non e' un rapporto illeggibile e non e' un rifiuto
        del pacchetto: e' la stessa cosa di una rete caduta, e si tratta
        cosi'. Un 403 che NON parla di quota resta quel che e' — un rifiuto
        certo, che il ramo «illeggibile» scrive per intero.
        """
        try:
            stato = int(risposta.stato)
        except (TypeError, ValueError):
            return None
        if stato != 403:
            return None
        messaggio = (risposta.messaggio or "").strip()
        if "quota" not in messaggio.lower():
            return None
        return _("la quota oraria di chiamate a Cdiscount e' esaurita: %s"
                 ) % messaggio

    def _riga_rapporto(self, pacchetto, numero, righe, conti, chiuso,
                       pagine, esistevano):
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
                + len(conti["estranee"]) + len(conti["attese"]))
        # ⚠️ Le mancanti VERE sono quelle che il rapporto non nomina: le
        # attese le ha nominate, e si dicono a parte.
        non_nominate = [c for c in conti["mancanti"]
                        if c not in set(conti["attese"])]
        if pagine == 1:
            in_pagine = _("in 1 pagina")
        else:
            in_pagine = _("in %s pagine") % pagine
        pezzi = [_(
            "Pacchetto %(numero)s: partite %(partite)s schede, il rapporto ha "
            "portato %(voci)s voci %(pagine)s — confermate %(confermate)s, "
            "rifiutate %(rifiutate)s, senza verdetto %(mancanti)s.")
            % {"numero": numero, "partite": partite, "voci": voci,
               "pagine": in_pagine,
               "confermate": conti["confermati"],
               "rifiutate": conti["rifiutati"],
               "mancanti": len(non_nominate)}]
        if esistevano:
            pezzi.append(_(
                "Di quelle confermate, esistevano gia' %s sul catalogo di "
                "Cdiscount (la scheda e' stata arricchita o era identica): "
                "lo dice la colonna «Su Cdiscount».") % esistevano)
        if conti["attese"]:
            pezzi.append(_(
                "⚠️ Cdiscount sta ancora integrando %(quante)s schede "
                "validate: %(elenco)s. Restano «in attesa» e il pacchetto "
                "resta aperto; si ripassa.")
                % {"quante": len(conti["attese"]),
                   "elenco": _elenco_corto(conti["attese"])})
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
        if non_nominate:
            pezzi.append(_(
                "⚠️ Il rapporto NON nomina %(quante)s schede che erano "
                "partite: %(elenco)s. Restano «in attesa» e il pacchetto "
                "resta aperto — si riprova al giro dopo, e se l'esito non "
                "arriva scade il %(scade)s.")
                % {"quante": len(non_nominate),
                   "elenco": _elenco_corto(non_nominate),
                   "scade": self._ora_locale(pacchetto.scade_il)})
            # ⚠️ E LA DIAGNOSI DEL RAPPORTO MUTO, solo se non ha nominato
            # NESSUNA delle nostre — nemmeno come attesa.
            if not (conti["confermati"] or conti["rifiutati"]
                    or conti["attese"]):
                # ⚠️ ED E' UNA DIAGNOSI DIVERSA, non un parziale piu' grande.
                # Un rapporto che nomina ALCUNE delle nostre e' una notizia su
                # questo pacchetto; uno che non ne nomina NESSUNA e' una
                # notizia su tutti. Le due righe non si confondono a colpo
                # d'occhio apposta.
                pezzi.append(_(
                    "⚠️⚠️ IL RAPPORTO NON NOMINA NEMMENO UNA delle schede "
                    "partite, ed e' arrivato COMPLETO: non e' un rapporto a "
                    "meta'. Il riferimento con cui riconosciamo una scheda si "
                    "cerca sotto «%(chiavi)s»: il primo e' il nome "
                    "misurato sul vero il 2026-09-02, il secondo quello "
                    "della prima documentazione. Ne e' aperta "
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
                    _("Partite e mai nominate dal rapporto: %(mancanti)s, di "
                      "cui validate da Cdiscount e in corso di integrazione "
                      "%(attese)s.")
                    % {"mancanti": esito["mancanti"],
                       "attese": esito.get("attese", 0)},
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

    # ==================================================================
    # IL RIAGGANCIO — cosa esiste GIA' su Cdiscount
    #
    # Vedi docs/superpowers/plans/2026-09-02-cdiscount-solo-offerte.md e
    # docs/cdiscount-misurato-portale-2026-09-02.md §8.
    # ==================================================================
    def riaggancia(self):
        """Legge i prodotti gia' su Cdiscount e li accoppia ai nostri per SKU.

        ⚠️ **NON scrive niente su Cdiscount.** E' il gemello di
        `kaufland.riaggancia`, e nasce dalla stessa lezione: «creare offerte
        prima di sapere quali esistono significa duplicarle su un marketplace
        vero».

        ⚠️ Perche' serve QUI. Il catalogo Octopia e' condiviso, una scheda per
        GTIN, e le schede si creano dal portale — non da questo modulo. Il
        primo caricamento vero (2026-09-02) l'ha detto in due modi: **76
        prodotti su 176 erano gia' vendibili** senza che avessimo mandato
        niente, e 88 righe sono tornate con «non sei il creatore della
        scheda». Senza riaggancio il modulo non sa cosa esiste, e
        `_assicura_offerte` non avrebbe da cosa nascere.

        ⚠️ **Ogni riga letta finisce in UN secchio, e i secchi sommano a
        `lette`.** Una riga che non finisce da nessuna parte e' una riga di
        cui non sappiamo dire niente, e su un «non lo so» non si aggancia. Se
        Octopia rinominasse `sellerProductReference`, cadrebbero tutte negli
        scarti: il conto tornerebbe, ma `agganciate: 0` su `lette: 900` e' una
        notizia che si legge a colpo d'occhio.

        Rende `{"lette", "agganciate", "senza_prodotto", "non_vendibili",
        "contese", "scartate", "senza"}`.
        """
        self._gettone()
        client = self._client()
        Scheda = self.env["cdiscount.scheda"].sudo()
        Prodotto = self.env["product.product"].sudo()
        azienda = self.channel.company_id
        # ⚠️ Il nostro codice venditore serve a scegliere la riga giusta
        # dentro `sellers[]`: il catalogo e' condiviso e su una scheda ci sono
        # anche gli altri. Senza, si aggancerebbe il codice interno di un
        # concorrente al nostro prodotto.
        venditore = _testo(self.channel.sudo().cdiscount_seller_id)
        adesso = fields.Datetime.now()
        esito = {"lette": 0, "agganciate": 0, "senza_prodotto": 0,
                 "non_vendibili": 0, "contese": 0, "scartate": 0}
        senza = []
        contese_dette = []

        # ⚠️ Il savepoint rende locale l'intenzione «meglio niente che meta'»:
        # meta' riaggancio e' peggio di nessuno, perche' le offerte
        # nascerebbero da una fotografia parziale del catalogo.
        with self.env.cr.savepoint():
            for pagina in pagine_prodotti(client):
                for riga in pagina:
                    esito["lette"] += 1
                    codice, riferimento, vendibile, gtin = leggi_prodotto(
                        riga, venditore)
                    if not codice and not gtin:
                        # Senza né il nostro codice né il GTIN non c'e' niente
                        # a cui agganciarsi.
                        esito["scartate"] += 1
                        continue
                    if not vendibile:
                        esito["non_vendibili"] += 1
                        continue
                    # ⚠️ Il dominio azienda: in `sudo()` la ricerca vede anche
                    # i prodotti delle altre aziende, e un `default_code`
                    # omonimo finirebbe agganciato al canale sbagliato.
                    #
                    # ⚠️ E il controllo si RIFÀ dopo, sul record trovato: il
                    # dominio da solo non e' bastato (prove del 2026-09-02
                    # sera). `company_id` su `product.product` e' un related
                    # del template, e un related puo' comportarsi diversamente
                    # in ricerca a seconda di come e' dichiarato. Qui il
                    # prezzo di una cintura in piu' e' nullo; il prezzo di un
                    # prodotto agganciato al canale sbagliato e' un'offerta
                    # pubblicata sul prodotto di un'altra azienda.
                    prodotto = Prodotto.browse()
                    if codice:
                        prodotto = Prodotto.search(
                            [("default_code", "=", codice),
                             ("company_id", "in", [False, azienda.id])],
                            limit=1)
                    # ⚠️ Il RIPIEGO SUL GTIN, e non e' un lusso: per i
                    # prodotti creati da ALTRI venditori la documentazione
                    # avverte che «some fields will not be displayed» e
                    # `sellers[]` puo' mancare — cioe' il nostro codice non
                    # c'e'. Il GTIN invece c'e' sempre, e in Odoo e' il codice
                    # a barre. Senza questo ripiego i prodotti gia' vendibili
                    # — 76 su 176 al primo caricamento, la maggioranza —
                    # resterebbero irraggiungibili.
                    if not prodotto and gtin:
                        prodotto = Prodotto.search(
                            [("barcode", "=", gtin),
                             ("company_id", "in", [False, azienda.id])],
                            limit=1)
                        if prodotto and not codice:
                            codice = _testo(prodotto.default_code)
                    if prodotto and prodotto.company_id \
                            and prodotto.company_id != azienda:
                        prodotto = Prodotto.browse()
                    # Un prodotto trovato per GTIN ma senza riferimento
                    # interno in Odoo non ha una chiave con cui vivere: e'
                    # l'offerta che ha bisogno del codice, non il riaggancio.
                    if prodotto and not codice:
                        esito["scartate"] += 1
                        continue
                    if not prodotto:
                        # Roba in vendita su Cdiscount che in Odoo non
                        # esiste: e' una notizia, non un dettaglio.
                        esito["senza_prodotto"] += 1
                        if len(senza) < MAX_CODICI_NEL_MESSAGGIO:
                            senza.append(codice)
                        continue
                    scheda = Scheda.search(
                        [("channel_id", "=", self.channel.id),
                         ("codice", "=", codice)], limit=1)
                    # ⚠️ La contesa si DICE e non si risolve a caso: due
                    # codici diversi che puntano allo stesso prodotto sono un
                    # dato sbagliato da qualche parte, e sceglierne uno
                    # nasconderebbe il problema invece di mostrarlo. E'
                    # anche cio' che evita di far arrivare dal DATABASE il
                    # vincolo unico, che in Odoo annulla l'INTERA transazione:
                    # si perderebbero anche le righe gia' agganciate bene.
                    altra = Scheda.search(
                        [("channel_id", "=", self.channel.id),
                         ("product_id", "=", prodotto.id),
                         ("codice", "!=", codice)], limit=1)
                    if altra:
                        esito["contese"] += 1
                        if len(contese_dette) < MAX_CODICI_NEL_MESSAGGIO:
                            contese_dette.append(
                                "%s/%s" % (codice, altra.codice))
                        continue
                    valori = {"product_id": prodotto.id,
                              "riferimento_octopia": riferimento,
                              "vendibile": True,
                              "stato": RIUSCITO,
                              "agganciata_il": adesso}
                    if scheda:
                        scheda.write(valori)
                    else:
                        valori.update({"channel_id": self.channel.id,
                                       "codice": codice})
                        Scheda.create(valori)
                    esito["agganciate"] += 1

        if not secchi_tornano(esito):
            raise UserError(_(
                "Il riaggancio ha letto %(lette)s righe ma ne ha classificate "
                "%(somma)s: c'e' almeno una riga di cui non sa dire niente. "
                "Non si aggancia su una lettura che non torna.")
                % {"lette": esito["lette"],
                   "somma": (esito["agganciate"] + esito["senza_prodotto"]
                             + esito["non_vendibili"] + esito["contese"]
                             + esito["scartate"])})
        esito["senza"] = senza
        esito["contese_dette"] = contese_dette
        # ⚠️ «error» e non «warning»: `centrivo.job.log.result` e' una
        # Selection con TRE voci — success, error, skip — e «warning» non
        # esiste. Un valore fuori elenco solleva, cioe' un riaggancio andato
        # bene fallirebbe nel momento in cui prova a raccontarlo.
        #
        # ⚠️ E un riaggancio che non aggancia NIENTE e' rosso, non giallo: e'
        # il modo in cui questo giro puo' mentire, e va guardato.
        self._registra(
            "cdiscount_riaggancio",
            "success" if esito["agganciate"] else "error",
            _("Riaggancio: lette %(lette)s, agganciate %(agganciate)s, "
              "senza prodotto %(senza)s, non vendibili %(nonvend)s, "
              "contese %(contese)s, scartate %(scartate)s.")
            % {"lette": esito["lette"], "agganciate": esito["agganciate"],
               "senza": esito["senza_prodotto"],
               "nonvend": esito["non_vendibili"],
               "contese": esito["contese"], "scartate": esito["scartate"]})
        return esito

    # ==================================================================
    # CONSEGNA 2 — LE OFFERTE (prezzo, giacenza, éco-participation)
    #
    # Vedi docs/cdiscount-offerte-consegna-2.md. Il ciclo e' a quattro
    # chiamate (LETTO): nasce il pacchetto, si caricano le offerte a lotti
    # da 100, lo si manda in lavorazione, e il raccoglitore ne legge l'esito.
    # I modi di consegna sono MISURATI, tutto cio' che scrive no.
    # ==================================================================
    def _modo_consegna_verificato(self, client, codice):
        """Il modo di consegna del canale, LETTO da Cdiscount prima di
        scrivere. Rende il dizionario del modo (con la bandiera dei 30 kg).

        ⚠️ MISURATO il 2026-09-02: `GET /sellers/delivery-modes` risponde
        `{"count", "items": [{"code", "name", "more_than30_kg_product",
        …}]}` e sul nostro account i codici sono `TRK` e `REG` — NON i
        `THD/EHD/SHD` della documentazione. Un codice che l'account non ha e'
        un rifiuto garantito, e si scopre tre giorni dopo: qui si ferma
        prima. Tutto cio' che non e' «letto e presente» ferma l'invio.
        """
        canale = self.channel.display_name
        risposta = client.chiama("GET", API_MODI_CONSEGNA)
        incerta = (self._causa_incerta(risposta)
                   or self._quota_esaurita(risposta))
        if incerta:
            raise UserError(_(
                "I modi di consegna del canale «%(canale)s» non si e' "
                "potuto leggere da Cdiscount: %(perche)s. Senza, non parte "
                "nessuna offerta. Riprova fra poco.")
                % {"canale": canale, "perche": incerta})
        if not risposta.ok:
            raise UserError(_(
                "I modi di consegna del canale «%(canale)s» non si e' "
                "potuto leggere: Cdiscount ha risposto %(motivo)s.")
                % {"canale": canale,
                   "motivo": self._motivo_stato(risposta.stato,
                                                risposta.messaggio)})
        elenco = risposta.dati if isinstance(risposta.dati, list) else []
        modo = modo_consegna(elenco, codice)
        if modo is None:
            raise UserError(_(
                "Il modo di consegna «%(codice)s» del canale «%(canale)s» "
                "non esiste sull'account Cdiscount, che ne ha questi: "
                "%(disponibili)s. Con un codice che l'account non ha, ogni "
                "offerta verrebbe rifiutata. Va scelto uno di quelli.")
                % {"codice": codice, "canale": canale,
                   "disponibili": ", ".join(
                       "%s (%s)" % (_testo(m.get("code")),
                                    _testo(m.get("name")))
                       for m in elenco if isinstance(m, dict)) or "nessuno"})
        return modo

    def _assicura_offerte(self, Offerta, Scheda):
        """Una riga di offerta per ogni scheda riuscita con un prodotto.

        ⚠️ Le righe nascono qui, non da un file: l'offerta e' la conseguenza
        della scheda. Una scheda che smette di essere riuscita NON cancella
        la riga: e' cosi' che il ritiro sa cosa ritirare.
        """
        riuscite = Scheda.search([("channel_id", "=", self.channel.id),
                                  ("stato", "=", RIUSCITO),
                                  ("product_id", "!=", False)])
        esistenti = {_testo(o.codice): o for o in Offerta.search(
            [("channel_id", "=", self.channel.id)])}
        nuove = []
        for scheda in riuscite:
            codice = _testo(scheda.codice)
            if not codice:
                continue
            riga = esistenti.get(codice)
            if riga is None:
                nuove.append({"channel_id": self.channel.id,
                              "codice": codice, "scheda_id": scheda.id,
                              "product_id": scheda.product_id.id})
            elif riga.scheda_id != scheda or riga.product_id != scheda.product_id:
                riga.write({"scheda_id": scheda.id,
                            "product_id": scheda.product_id.id})
        if nuove:
            Offerta.create(nuove)

    def _salta_offerta(self, riga, motivo, esito, motivi):
        esito["saltate"] += 1
        motivi.append("%s: %s" % (_testo(riga.codice), motivo))
        self._al_riparo(riga.write, {"motivo": motivo})

    def _cambi_offerte(self, candidate, listino, modo, esito, motivi, tetto):
        """Cosa parte, riga per riga: `[(riga, corpo, valori)]`.

        Ogni riga finisce in UNO di questi esiti: un cambio da mandare,
        «invariata», o «saltata» col suo motivo — una riga che non finisce
        da nessuna parte e' una riga di cui non sappiamo dire niente.
        """
        canale = self.channel.sudo()
        cambi = []
        troncato = False
        for riga in candidate:
            if len(cambi) >= tetto:
                troncato = True
                break
            scheda = riga.scheda_id
            prodotto = riga.product_id
            viva = bool(scheda and _testo(scheda.stato) == RIUSCITO
                        and prodotto and prodotto.active)
            if not viva:
                # ⚠️ IL RITIRO: quantita' zero, una volta sola, su cio' che
                # era stato mandato. Una riga mai mandata o gia' ritirata
                # non ha niente da ritirare.
                if _testo(riga.stato) == RITIRATA or not riga.mandata_il:
                    esito["invariate"] += 1
                    continue
                if not prodotto or not riga.ultimo_prezzo:
                    self._salta_offerta(riga, _(
                        "da ritirare (la scheda non e' piu' riuscita), ma "
                        "senza prodotto o senza un prezzo gia' mandato non "
                        "si compone un ritiro: va guardata a mano sul "
                        "portale."), esito, motivi)
                    continue
                valori = {"prezzo": riga.ultimo_prezzo, "quantita": 0,
                          "ecotax": riga.ultima_ecotax, "ritiro": True}
            else:
                prezzo = self._pricelist_price(listino, prodotto)
                try:
                    quantita = int(self._available_quantity(prodotto,
                                                            canale) or 0)
                except Exception as errore:  # noqa: BLE001
                    self._salta_offerta(riga, _(
                        "giacenza non leggibile (%s).") % errore, esito,
                        motivi)
                    continue
                ecotax = prodotto.cdiscount_ecotax or 0.0
                if canale.cdiscount_ecotax_obbligatoria and not ecotax > 0:
                    self._salta_offerta(riga, _(
                        "manca l'éco-participation Cdiscount sul prodotto, "
                        "e il canale la pretende. Va compilata sul prodotto "
                        "(«Éco-participation Cdiscount»), o si spegne "
                        "«Pretendi l'éco-participation» sul canale."),
                        esito, motivi)
                    continue
                peso = prodotto.weight or 0.0
                if peso > SOGLIA_KG and not regge_oltre_30kg(modo):
                    self._salta_offerta(riga, _(
                        "pesa %(peso)s kg, oltre i %(soglia)s, e il modo di "
                        "consegna «%(modo)s» non regge i colli oltre i 30 "
                        "kg: finche' l'account non ha un modo «Big parcel», "
                        "questo prodotto non si puo' vendere.")
                        % {"peso": peso, "soglia": SOGLIA_KG,
                           "modo": _testo(modo.get("code"))}, esito, motivi)
                    continue
                valori = {"prezzo": prezzo, "quantita": max(quantita, 0),
                          "ecotax": ecotax, "ritiro": False}
            try:
                corpo = corpo_offerta(
                    codice=riga.codice, gtin=prodotto.barcode,
                    prezzo=valori["prezzo"], iva=canale.cdiscount_iva,
                    ecotax=valori["ecotax"], quantita=valori["quantita"],
                    modo=_testo(modo.get("code")),
                    costo=canale.cdiscount_spedizione_costo,
                    costo_aggiuntivo=canale.cdiscount_spedizione_costo_aggiuntivo,
                    giorni_preparazione=canale.processing_time_default)
            except ValueError as errore:
                self._salta_offerta(riga, str(errore), esito, motivi)
                continue
            # ⚠️ Cosa si rimanda: mai mandata, oppure valori diversi dagli
            # ultimi mandati. Una rifiutata con gli STESSI valori NON si
            # ripete a ogni giro: Cdiscount ha gia' detto di no.
            invariata = (bool(riga.mandata_il)
                         and _testo(riga.stato) in (RIUSCITO, RIFIUTATO,
                                                    RITIRATA)
                         and corpo["price"]["price"] == round(
                             riga.ultimo_prezzo or 0, 2)
                         and corpo["quantity"] == (riga.ultima_quantita or 0)
                         and corpo["price"]["taxes"][1]["value"] == round(
                             riga.ultima_ecotax or 0, 2)
                         and bool(riga.ritiro) == valori["ritiro"])
            if invariata:
                esito["invariate"] += 1
                continue
            valori["prezzo"] = corpo["price"]["price"]
            valori["quantita"] = corpo["quantity"]
            valori["ecotax"] = corpo["price"]["taxes"][1]["value"]
            cambi.append((riga, corpo, valori))
        return cambi, troncato

    def allinea_offerte(self, limite=None):
        """Manda a Cdiscount le offerte cambiate, in UN pacchetto `Upsert`.

        `limite` conta le OFFERTE e serve alla prima prova sul vero: si parte
        da una sola, si guarda sul portale, e solo dopo si manda il resto.

        L'ordine, e non e' negoziabile:

        1. il turno; le guardie (credenziali, canale di vendita, listino,
           modo di consegna); il gettone; **il modo di consegna letto da
           Cdiscount**;
        2. le righe: una per scheda riuscita, poi i cambi (riga per riga:
           un cambio, «invariata», o «saltata» col motivo);
        3. `POST /offer-packages` → il numero (dal `Content-Location`), che
           si scrive SUBITO;
        4. i lotti da 100: un lotto rifiutato segna le sue righe e continua,
           un lotto incerto segna le righe «in attesa» e smette di caricare;
        5. `PATCH Ready`: se fallisce, il pacchetto resta scritto con
           `pronto = False` e il raccoglitore ritenta.
        """
        self._prendi_il_turno(_("allineamento delle offerte"))
        canale = self.channel.sudo()
        self._canale_vendita()
        listino = canale.pricelist_selling_id
        if not listino:
            raise UserError(_(
                "Sul canale «%s» manca il listino («Listino prezzo pieno»): "
                "senza, non c'e' nessun prezzo da mandare a Cdiscount.")
                % self.channel.display_name)
        codice_modo = _testo(canale.cdiscount_modo_consegna)
        if not codice_modo:
            raise UserError(_(
                "Sul canale «%s» manca il modo di consegna: Cdiscount rifiuta "
                "le offerte senza. Sul nostro account sono TRK (Envoi Suivi) "
                "e REG (Recommandé).") % self.channel.display_name)
        client = self._client()
        self._gettone()
        modo = self._modo_consegna_verificato(client, codice_modo)

        Offerta = self.env["cdiscount.offerta"].sudo()
        Scheda = self.env["cdiscount.scheda"].sudo()
        self._assicura_offerte(Offerta, Scheda)
        esito = {"mandate": 0, "ritiri": 0, "pacchetti": 0, "saltate": 0,
                 "invariate": 0, "rifiutate": 0, "incerte": 0,
                 "non_partite": 0, "rimaste": 0, "modo": codice_modo}
        motivi = []
        fermata = None
        tetto = min(limite, MAX_OFFERTE_PER_GIRO) if limite else MAX_OFFERTE_PER_GIRO
        candidate = Offerta.search([("channel_id", "=", self.channel.id),
                                    ("stato", "!=", IN_ATTESA)], order="id")
        cambi, troncato = self._cambi_offerte(candidate, listino, modo,
                                              esito, motivi, tetto)
        if troncato and not limite:
            fermata = _("Il giro si e' fermato al tetto di %s offerte per "
                        "volta. Ripetere per continuare.") % MAX_OFFERTE_PER_GIRO
        if not cambi:
            return self._chiudi_allineamento(esito, motivi, fermata)

        # 3) Nasce il pacchetto. Finche' non ci si carica niente, non c'e'
        #    niente a rischio: un pacchetto vuoto scade da solo in sei ore.
        risposta = client.chiama("POST", API_OFFER_PACKAGES,
                                 {"packageType": "Upsert"})
        if getattr(risposta, "prevolo", False):
            esito["non_partite"] += len(cambi)
            fermata = _("Il giro si e' fermato PRIMA di chiamare Cdiscount: "
                        "%s. Non e' partito niente.") % risposta.messaggio
            return self._chiudi_allineamento(esito, motivi, fermata)
        incerta = (self._causa_incerta(risposta)
                   or self._quota_esaurita(risposta))
        if incerta or not risposta.ok:
            esito["non_partite"] += len(cambi)
            fermata = _("Il pacchetto di offerte non e' nato: %s. Non e' "
                        "partito niente, si ripete piu' tardi.") % (
                incerta or self._motivo_stato(risposta.stato,
                                              risposta.messaggio))
            self._al_riparo(self._registra, OPERAZIONE_ALLINEA, "error",
                            fermata)
            return self._chiudi_allineamento(esito, motivi, fermata)
        numero = numero_pacchetto_offerte(risposta.teste, risposta.corpo)
        if not numero:
            esito["non_partite"] += len(cambi)
            fermata = _("Cdiscount ha creato un pacchetto di offerte ma la "
                        "risposta non porta nessun numero (%s). Non ci si "
                        "e' caricato niente: si ripete piu' tardi.") % (
                (risposta.testo or str(risposta.teste))[:300])
            self._al_riparo(self._registra, OPERAZIONE_ALLINEA, "error",
                            fermata)
            return self._chiudi_allineamento(esito, motivi, fermata)
        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        scade = adesso + timedelta(days=GIORNI_ESITO)
        scritto = self._al_riparo(self._crea_pacchetto, numero, adesso,
                                  scade, TIPO_OFFERTE, False)
        pacchetto = self._pacchetto_scritto(numero) if scritto else None
        if not pacchetto:
            esito["non_partite"] += len(cambi)
            fermata = _("Il pacchetto di offerte %s e' nato su Cdiscount ma "
                        "il suo numero non si e' potuto scrivere in Odoo. "
                        "Non ci si e' caricato niente: scadra' da solo. Si "
                        "ripete piu' tardi.") % numero
            self._al_riparo(self._registra, OPERAZIONE_ALLINEA, "error",
                            fermata, None, numero)
            return self._chiudi_allineamento(esito, motivi, fermata)
        esito["pacchetti"] = 1

        # 4) I lotti.
        try:
            gruppi = lotti([corpo for _r, corpo, _v in cambi])
        except ValueError as errore:
            esito["non_partite"] += len(cambi)
            fermata = str(errore)
            return self._chiudi_allineamento(esito, motivi, fermata)
        indice = 0
        caricate = 0
        percorso_pacchetto = "%s/%s" % (API_OFFER_PACKAGES, quote(numero, safe=""))
        for gruppo in gruppi:
            pezzo = cambi[indice:indice + len(gruppo)]
            indice += len(gruppo)
            righe = Offerta.browse([r.id for r, _c, _v in pezzo])
            risposta = client.chiama("POST", "%s/offer-requests"
                                     % percorso_pacchetto, gruppo)
            if getattr(risposta, "prevolo", False):
                esito["non_partite"] += len(pezzo)
                fermata = _("Un lotto non e' partito (guasto dal nostro "
                            "lato): %s.") % risposta.messaggio
                break
            incerta = (self._causa_incerta(risposta)
                       or self._quota_esaurita(risposta))
            if incerta:
                # ⚠️ Cdiscount puo' averlo preso: le righe passano in attesa
                # col pacchetto, e l'esito dira'. Non si carica altro.
                esito["incerte"] += len(pezzo)
                self._segna_mandate(pezzo, pacchetto, adesso)
                caricate += len(pezzo)
                fermata = _("Esito IGNOTO su un lotto di %(quante)s offerte: "
                            "%(perche)s. Restano «in attesa» e il "
                            "raccoglitore leggera' l'esito del pacchetto.") % {
                    "quante": len(pezzo), "perche": incerta}
                break
            if not risposta.ok:
                motivo = self._motivo_stato(risposta.stato, risposta.messaggio)
                esito["rifiutate"] += len(pezzo)
                motivi.append(_("lotto di %(quante)s offerte rifiutato: "
                                "%(motivo)s") % {"quante": len(pezzo),
                                                 "motivo": motivo})
                self._al_riparo(righe.write, {
                    "stato": RIFIUTATO, "controllato_il": adesso,
                    "motivo": _("Cdiscount ha rifiutato il LOTTO intero in "
                                "cui questa offerta era: %s") % motivo})
                continue
            self._segna_mandate(pezzo, pacchetto, adesso)
            caricate += len(pezzo)
            esito["mandate"] += len(pezzo)
            esito["ritiri"] += sum(1 for _r, _c, v in pezzo if v["ritiro"])

        # 5) In lavorazione.
        if caricate:
            risposta = client.chiama("PATCH", percorso_pacchetto,
                                     {"state": "Ready"})
            if risposta.ok:
                self._al_riparo(pacchetto.write, {"pronto": True})
            else:
                perche = (self._causa_incerta(risposta)
                          or self._quota_esaurita(risposta)
                          or self._motivo_stato(risposta.stato,
                                                risposta.messaggio))
                fermata = _("Il pacchetto %(numero)s e' caricato ma NON e' "
                            "stato mandato in lavorazione (%(perche)s): il "
                            "raccoglitore ritentera'. ⚠️ Cdiscount lo "
                            "aspetta entro sei ore.") % {"numero": numero,
                                                          "perche": perche}
        return self._chiudi_allineamento(esito, motivi, fermata)

    def _segna_mandate(self, pezzo, pacchetto, adesso):
        """Le righe di un lotto partito: in attesa, col pacchetto e la
        memoria di cio' che e' partito."""
        for riga, _corpo, valori in pezzo:
            self._al_riparo(riga.write, {
                "stato": IN_ATTESA, "pacchetto_id": pacchetto.id,
                "ultimo_prezzo": valori["prezzo"],
                "ultima_quantita": valori["quantita"],
                "ultima_ecotax": valori["ecotax"],
                "ritiro": valori["ritiro"], "mandata_il": adesso,
                "motivo": False})

    def _chiudi_allineamento(self, esito, motivi, fermata):
        """Tira le somme dell'allineamento e lascia una riga nel registro."""
        esito["fermata"] = fermata or False
        try:
            with self.env.cr.savepoint():
                Offerta = self.env["cdiscount.offerta"].sudo()
                esito["rimaste"] = Offerta.search_count(
                    [("channel_id", "=", self.channel.id),
                     ("stato", "=", DA_MANDARE)])
                verde = not (esito["saltate"] or esito["rifiutate"]
                             or esito["incerte"] or esito["non_partite"]
                             or fermata)
                pezzi = [_(
                    "Mandate %(mandate)s offerte (di cui %(ritiri)s ritiri) "
                    "in %(pacchetti)s pacchetti, modo di consegna %(modo)s. "
                    "Invariate %(invariate)s, saltate %(saltate)s, in lotti "
                    "rifiutati %(rifiutate)s, di esito ignoto %(incerte)s, "
                    "non partite %(non_partite)s. Mai mandate finora: "
                    "%(rimaste)s.") % esito]
                if fermata:
                    pezzi.append(fermata)
                for motivo in motivi[:MAX_MOTIVI_NEL_REGISTRO]:
                    pezzi.append(motivo)
                if len(motivi) > MAX_MOTIVI_NEL_REGISTRO:
                    pezzi.append(_("… e altri %s motivi: ognuno sta sulla "
                                   "propria riga, nella pagina «Offerte "
                                   "Cdiscount».")
                                 % (len(motivi) - MAX_MOTIVI_NEL_REGISTRO))
                self._registra(OPERAZIONE_ALLINEA,
                               "success" if verde else "error",
                               "\n".join(pezzi))
        except Exception:  # noqa: BLE001
            _logger.exception(
                "Cdiscount sul canale %s: la chiusura dell'allineamento e' "
                "fallita. Esito grezzo: %s", self.channel.display_name, esito)
            esito.setdefault("rimaste", 0)
            esito["chiusura_fallita"] = 1
        return esito

    def _raccogli_offerte(self, pacchetto, numero, client):
        """L'esito di UN pacchetto di offerte. Rende `(conti, riga, nota)`.

        Prima il `PATCH Ready` se non e' mai passato, poi lo stato del
        pacchetto (`GET /offer-packages/{id}`), poi — solo se e' integrato —
        gli esiti riga per riga, pagina per pagina (cursore nel `Link`), e la
        stessa `riconcilia` delle schede.
        """
        Offerta = self.env["cdiscount.offerta"].sudo()
        righe = Offerta.search([("pacchetto_id", "=", pacchetto.id)])
        percorso = "%s/%s" % (API_OFFER_PACKAGES, quote(numero, safe=""))

        def incerto(perche):
            return ({"incerti": 1}, None, _(
                "Pacchetto di offerte %(numero)s: l'esito non si e' potuto "
                "leggere (%(perche)s). Resta aperto e si riprova; scade il "
                "%(scade)s.") % {"numero": numero, "perche": perche,
                                 "scade": self._ora_locale(pacchetto.scade_il)})

        if not pacchetto.pronto:
            risposta = client.chiama("PATCH", percorso, {"state": "Ready"})
            perche = (self._causa_incerta(risposta)
                      or self._quota_esaurita(risposta))
            if perche:
                return incerto(perche)
            if not risposta.ok:
                return ({"illeggibili": 1}, None, _(
                    "Pacchetto di offerte %(numero)s: Cdiscount non lo "
                    "accetta in lavorazione (%(motivo)s). Resta aperto; va "
                    "guardato prima che scada il %(scade)s.") % {
                        "numero": numero, "scade": self._ora_locale(pacchetto.scade_il),
                        "motivo": self._motivo_stato(risposta.stato,
                                                     risposta.messaggio)})
            pacchetto.write({"pronto": True})

        risposta = client.chiama("GET", percorso)
        perche = self._causa_incerta(risposta) or self._quota_esaurita(risposta)
        if perche:
            return incerto(perche)
        if not risposta.ok:
            return ({"illeggibili": 1}, None, _(
                "Pacchetto di offerte %(numero)s: lo stato non si e' potuto "
                "leggere (%(motivo)s). Resta aperto; scade il %(scade)s.") % {
                    "numero": numero, "scade": self._ora_locale(pacchetto.scade_il),
                    "motivo": self._motivo_stato(risposta.stato,
                                                 risposta.messaggio)})
        corpo_pacchetto = risposta.corpo
        stato = stato_pacchetto(corpo_pacchetto)
        adesso = fields.Datetime.to_datetime(fields.Datetime.now())
        if stato == IN_LAVORAZIONE:
            return ({"in_lavorazione": 1}, None, _(
                "Pacchetto di offerte %(numero)s: Cdiscount ci sta ancora "
                "lavorando (%(stato)s). Si ripassa; scade il %(scade)s.") % {
                    "numero": numero, "scade": self._ora_locale(pacchetto.scade_il),
                    "stato": _testo((corpo_pacchetto or {}).get("state")
                                    if isinstance(corpo_pacchetto, dict)
                                    else "")})
        if stato == RIFIUTATO_IN_BLOCCO:
            motivo = motivo_pacchetto(corpo_pacchetto)
            in_attesa = righe.filtered(lambda r: r.stato == IN_ATTESA)
            if in_attesa:
                in_attesa.write({"stato": RIFIUTATO, "controllato_il": adesso,
                                 "motivo": _("Cdiscount ha rifiutato il "
                                             "pacchetto INTERO: %s") % motivo})
            pacchetto.write({"stato": RACCOLTO})
            return ({"rifiutate": len(in_attesa), "chiusi": 1},
                    ("error", _("Pacchetto di offerte %(numero)s RIFIUTATO in "
                                "blocco da Cdiscount: %(motivo)s. Le sue "
                                "%(quante)s offerte sono rifiutate.") % {
                        "numero": numero, "motivo": motivo,
                        "quante": len(in_attesa)}), None)
        if stato != PRONTO:
            return ({"illeggibili": 1}, None, _(
                "Pacchetto di offerte %(numero)s: stato «%(stato)s» che non "
                "conosciamo. Resta aperto; va guardato prima che scada il "
                "%(scade)s.") % {"numero": numero, "scade": self._ora_locale(pacchetto.scade_il),
                                 "stato": (risposta.testo or "")[:200]})

        # Integrato: gli esiti, pagina per pagina.
        righe_esiti = []
        pagine = 0
        prossimo = "%s/offer-requests-results?%s" % (
            percorso, urlencode({"limit": MAX_PER_LOTTO}))
        while prossimo:
            pagine += 1
            if pagine > MAX_PAGINE_ESITI_OFFERTE:
                return incerto(_("piu' di %s pagine di esiti")
                               % MAX_PAGINE_ESITI_OFFERTE)
            risposta = client.chiama("GET", prossimo)
            perche = (self._causa_incerta(risposta)
                      or self._quota_esaurita(risposta))
            if perche:
                return incerto(perche)
            if not risposta.ok or not isinstance(risposta.dati, list):
                return ({"illeggibili": 1}, None, _(
                    "Pacchetto di offerte %(numero)s: gli esiti non si sono "
                    "potuti leggere (%(motivo)s). Resta aperto; scade il "
                    "%(scade)s.") % {
                        "numero": numero, "scade": self._ora_locale(pacchetto.scade_il),
                        "motivo": self._motivo_stato(risposta.stato,
                                                     risposta.messaggio)
                        if not risposta.ok else (risposta.testo or "")[:200]})
            righe_esiti.extend(risposta.dati)
            cursore = cursore_da_link(risposta.teste)
            prossimo = ("%s/offer-requests-results?%s" % (
                percorso, urlencode({"cursor": cursore,
                                     "limit": MAX_PER_LOTTO}))
                        if cursore else "")
        stato_esiti, esiti = leggi_esiti_offerte(righe_esiti)
        if stato_esiti != PRONTO:
            return ({"in_lavorazione": 1}, None, _(
                "Pacchetto di offerte %(numero)s: integrato ma senza esiti "
                "ancora. Si ripassa; scade il %(scade)s.") % {
                    "numero": numero, "scade": self._ora_locale(pacchetto.scade_il)})
        mandati = [_testo(r.codice) for r in righe]
        conti = riconcilia(mandati, esiti)
        mancanti = set(conti["mancanti"])
        vive, ritirate, per_motivo = [], [], {}
        for riga in righe:
            codice = _codice(riga.codice)
            if codice in mancanti:
                continue
            verdetto = esiti[codice]
            if verdetto["esito"] == RIUSCITO:
                (ritirate if riga.ritiro else vive).append(riga.id)
            else:
                per_motivo.setdefault(_testo(verdetto["motivo"]),
                                      []).append(riga.id)
        if vive:
            Offerta.browse(vive).write({"stato": RIUSCITO, "motivo": False,
                                        "controllato_il": adesso})
        if ritirate:
            Offerta.browse(ritirate).write({"stato": RITIRATA, "motivo": False,
                                            "controllato_il": adesso})
        for motivo, ids in per_motivo.items():
            Offerta.browse(ids).write({"stato": RIFIUTATO,
                                       "motivo": motivo or False,
                                       "controllato_il": adesso})
        chiuso = not conti["mancanti"]
        if chiuso:
            pacchetto.write({"stato": RACCOLTO})
        numeri = {"confermate": conti["confermati"],
                  "rifiutate": conti["rifiutati"],
                  "mancanti": len(conti["mancanti"]),
                  "estranee": len(conti["estranee"])}
        if chiuso:
            numeri["chiusi"] = 1
        pezzi = [_("Pacchetto di offerte %(numero)s: partite %(partite)s, "
                   "vive %(vive)s, ritirate %(ritirate)s, rifiutate "
                   "%(rifiutate)s, senza verdetto %(mancanti)s, voci estranee "
                   "%(estranee)s (%(pagine)s pagine di esiti).") % {
            "numero": numero, "partite": len(mandati), "vive": len(vive),
            "ritirate": len(ritirate), "rifiutate": conti["rifiutati"],
            "mancanti": len(conti["mancanti"]),
            "estranee": len(conti["estranee"]), "pagine": pagine}]
        if conti["mancanti"]:
            pezzi.append(_("⚠️ Senza verdetto: %s. Restano in attesa, il "
                           "pacchetto resta aperto.")
                         % _elenco_corto(conti["mancanti"]))
        if conti["estranee"]:
            pezzi.append(_("⚠️ Voci estranee: %s.")
                         % _elenco_corto(conti["estranee"]))
        pulito = not (conti["mancanti"] or conti["estranee"]
                      or conti["rifiutati"] or not righe)
        return (numeri, ("success" if pulito else "error", "\n".join(pezzi)),
                None)

    # ------------------------------------------------------------------
    # Il contratto della classe base
    # ------------------------------------------------------------------
    # ==================================================================
    # CONSEGNA 3 — GLI ORDINI
    #
    # Vedi docs/cdiscount-ordini-contratto.md. Come si chiede e' MISURATO
    # (paginazione a indice da 100, `status` validato, conteggio nudo), la
    # forma dell'ordine e' LETTA: il primo ordine vero la confermera'.
    # ==================================================================
    def pull_orders(self):
        """Scarica gli ordini in preparazione e li importa in Odoo.

        ⚠️ SOLO gli `InPreparation`: l'indirizzo di consegna esiste solo da
        li', ed e' l'unico stato in cui si puo' spedire. Prima si CONTANO
        quelli in attesa di accettazione: l'accettazione automatica e' accesa
        sul portale (misurato), ma se qualcuno la spegnesse gli ordini si
        fermerebbero li' in silenzio — e qui lo si dice, senza accettare al
        posto di nessuno.

        Ogni ordine sta nel suo savepoint: uno che rompe non ferma gli
        altri. Un elenco interrotto NON e' completo, e si dice: gli ordini
        letti fin li' si importano lo stesso (ognuno e' idempotente).
        """
        esito = {"lette": 0, "ordini": 0, "importati": 0, "gia_importati": 0,
                 "in_errore": 0, "saltati": 0, "in_attesa_accettazione": 0,
                 "interrotto": 0}
        note = []
        canale_vendita = self._canale_vendita()
        client = self._client()
        self._gettone()

        risposta = client.chiama("GET", PERCORSO_CONTEGGIO_IN_ATTESA)
        if risposta.ok and isinstance(risposta.corpo, (int, float)) \
                and not isinstance(risposta.corpo, bool):
            if risposta.corpo > 0:
                esito["in_attesa_accettazione"] = int(risposta.corpo)
                note.append(_(
                    "⚠️ %s ordini sono in attesa di ACCETTAZIONE su "
                    "Cdiscount, e da qui non si accettano: l'accettazione "
                    "automatica sul portale e' spenta? Finche' restano li', "
                    "non si possono ne' scaricare ne' spedire.")
                    % int(risposta.corpo))
        else:
            note.append(_(
                "il conteggio degli ordini in attesa di accettazione non si "
                "e' potuto leggere (%s).") % (
                self._causa_incerta(risposta)
                or self._motivo_stato(risposta.stato, risposta.messaggio)))

        ordini = []
        pagina = 0
        while True:
            pagina += 1
            if pagina > MAX_PAGINE_ORDINI:
                esito["interrotto"] = 1
                note.append(_("lo scarico si e' fermato a %s pagine: sono "
                              "piu' ordini di quanti abbia senso leggere in "
                              "un giro.") % MAX_PAGINE_ORDINI)
                break
            risposta = client.chiama("GET", percorso_ordini(pagina,
                                                            canale_vendita))
            perche = (self._causa_incerta(risposta)
                      or self._quota_esaurita(risposta))
            if not perche and not risposta.ok:
                perche = self._motivo_stato(risposta.stato, risposta.messaggio)
            if not perche and not isinstance(risposta.dati, list):
                perche = _("risposta senza l'elenco `items`")
            if perche:
                esito["interrotto"] = 1
                note.append(_(
                    "⚠️ lo scarico si e' interrotto alla pagina %(pagina)s: "
                    "%(perche)s. Gli ordini letti fin qui si importano lo "
                    "stesso; i restanti al prossimo giro.")
                    % {"pagina": pagina, "perche": perche})
                break
            ordini.extend(risposta.dati)
            if len(risposta.dati) < ORDINI_PER_PAGINA:
                break
        esito["lette"] = esito["ordini"] = len(ordini)

        for grezzo in ordini:
            numero = (_testo(grezzo.get("orderId"))
                      if isinstance(grezzo, dict) else "")
            try:
                with self.env.cr.savepoint():
                    self._importa_ordine(grezzo, esito, note)
            except Exception as errore:  # noqa: BLE001
                esito["in_errore"] += 1
                _logger.exception("Cdiscount: ordine %s non importato",
                                  numero or "?")
                self._al_riparo(self._segna_ordine_in_errore, numero or "?",
                                str(errore)[:2000])

        verde = not (esito["in_errore"] or esito["interrotto"]
                     or esito["in_attesa_accettazione"] or note)
        pezzi = [_("Ordini letti %(lette)s: importati %(importati)s, gia' "
                   "presenti %(gia_importati)s, in errore %(in_errore)s, "
                   "saltati (nessuna riga da importare) %(saltati)s.")
                 % esito]
        pezzi.extend(note)
        self._al_riparo(self._registra, "pull_orders",
                        "success" if verde else "error", "\n".join(pezzi))
        return esito

    def _segna_ordine_in_errore(self, numero, messaggio):
        """La riga di mappa in errore: e' il posto dove si va a guardare."""
        Mappa = self.env["centrivo.order.map"].sudo()
        mappa = Mappa.search([("channel_id", "=", self.channel.id),
                              ("external_id", "=", numero)], limit=1)
        valori = {"state": "error", "error_message": messaggio}
        if mappa:
            mappa.write(valori)
        else:
            Mappa.create(dict(valori, channel_id=self.channel.id,
                              external_id=numero,
                              company_id=self.channel.company_id.id))
        self._registra("pull_orders", "error",
                       _("Ordine %(numero)s: %(messaggio)s")
                       % {"numero": numero, "messaggio": messaggio},
                       external_id=numero)
        return True

    @staticmethod
    def _data_iso(testo):
        """Una data ISO 8601 di Octopia (`2026-09-04T00:00:00+00:00`) come
        Datetime di Odoo, o False. Una data storta non ferma un ordine."""
        if not testo:
            return False
        try:
            pulito = str(testo).replace("Z", "")
            if "+" in pulito:
                pulito = pulito.split("+", 1)[0]
            return fields.Datetime.to_datetime(pulito.replace("T", " ")[:19])
        except Exception:  # noqa: BLE001
            return False

    def _importa_ordine(self, grezzo, esito, note):
        """Un ordine Cdiscount diventa un `sale.order`. Idempotente.

        ⚠️ **Prodotto NON mappato → l'ordine va in errore, e si vede.** Non
        si crea un prodotto da un ordine. ⚠️ Prodotto senza giacenza → entra
        lo stesso: la merce che manca si guarda in magazzino.
        """
        numero = (_testo(grezzo.get("orderId"))
                  if isinstance(grezzo, dict) else "") or "?"
        try:
            letto = leggi_ordine(grezzo)
        except ValueError as errore:
            esito["in_errore"] += 1
            self._segna_ordine_in_errore(numero, str(errore))
            return
        numero = letto["numero"]
        Mappa = self.env["centrivo.order.map"].sudo()
        mappa = Mappa.search([("channel_id", "=", self.channel.id),
                              ("external_id", "=", numero)], limit=1)
        if mappa and mappa.state == "imported":
            esito["gia_importati"] += 1
            return
        if not letto["righe"]:
            esito["saltati"] += 1
            note.append(_(
                "ordine %(numero)s: nessuna riga da importare (%(perche)s).")
                % {"numero": numero,
                   "perche": "; ".join("%s: %s" % (e["id"], e["perche"])
                                       for e in letto["escluse"])
                   or _("nessuna riga")})
            return

        Prodotto = self.env["product.product"].sudo()
        azienda = self.channel.company_id
        mancanti, prodotti = [], {}
        for riga in letto["righe"]:
            prodotto = Prodotto.search(
                [("default_code", "=", riga["codice"]),
                 ("company_id", "in", [False, azienda.id])], limit=1)
            if not prodotto and riga["gtin"]:
                prodotto = Prodotto.search(
                    [("barcode", "=", riga["gtin"]),
                     ("company_id", "in", [False, azienda.id])], limit=1)
            if not prodotto:
                mancanti.append(riga["codice"])
            else:
                prodotti[riga["id"]] = prodotto
        if mancanti:
            esito["in_errore"] += 1
            self._segna_ordine_in_errore(numero, _(
                "Nessun prodotto in Odoo con codice %s: l'ordine non e' "
                "stato importato. Il prodotto non si crea da un ordine — va "
                "creato o corretto il codice, poi si ripete lo scarico.")
                % ", ".join(sorted(set(mancanti))))
            return

        canale = self.channel.sudo()
        cliente = self._cliente_cdiscount(letto)
        consegna = cliente
        if not letto["consegna_uguale"]:
            consegna = self._indirizzo_consegna(cliente, letto["consegna"])
        righe_ordine = [(0, 0, {
            "product_id": prodotti[riga["id"]].id,
            "product_uom_qty": riga["quantita"],
            "price_unit": riga["prezzo"],
        }) for riga in letto["righe"]]
        spese = spedizione_righe(letto["righe"])
        if spese > 0:
            if canale.cdiscount_prodotto_spedizione:
                righe_ordine.append((0, 0, {
                    "product_id": canale.cdiscount_prodotto_spedizione.id,
                    "product_uom_qty": 1,
                    "price_unit": spese,
                }))
            else:
                note.append(_(
                    "⚠️ ordine %(numero)s: %(spese).2f € di spese di "
                    "spedizione pagate dal cliente NON sono entrate "
                    "nell'ordine: sul canale manca il «Prodotto spese di "
                    "spedizione».") % {"numero": numero, "spese": spese})
        if not canale.cdiscount_posizione_fiscale:
            note.append(_(
                "⚠️ ordine %s importato SENZA posizione fiscale: sul canale "
                "manca la «Posizione fiscale (Francia)», e l'IVA applicata "
                "potrebbe non essere quella francese.") % numero)
        ordine = self.env["sale.order"].sudo().create({
            "partner_id": cliente.id,
            "partner_invoice_id": cliente.id,
            "partner_shipping_id": consegna.id,
            "company_id": azienda.id,
            "team_id": self.channel.team_id.id or False,
            "fiscal_position_id": canale.cdiscount_posizione_fiscale.id or False,
            "client_order_ref": numero,
            "origin": "Cdiscount %s" % numero,
            "order_line": righe_ordine,
        })
        ordine.action_confirm()
        self._controlla_totale_ordine(ordine, letto, note)
        valori = {"state": "imported", "sale_order_id": ordine.id,
                  "error_message": False}
        if mappa:
            mappa.write(valori)
        else:
            mappa = Mappa.create(dict(
                valori, channel_id=self.channel.id, external_id=numero,
                company_id=azienda.id))
        self._registra_righe_ordine(mappa, ordine, letto, prodotti)
        esito["importati"] += 1

    def _controlla_totale_ordine(self, ordine, letto, note):
        """Il totale in Odoo deve tornare con quello pagato dal cliente.

        L'ordine resta: esiste, il cliente ha comprato. Ma la differenza va
        nel registro, perche' un ordine che vale meno (o piu') di quel che il
        cliente ha pagato e' un errore che si scopre in contabilita'.
        """
        atteso = letto["totale"]
        if atteso is None:
            atteso = totale_righe(letto["righe"])
        trovato = ordine.amount_total
        if abs(atteso - trovato) <= 0.01:
            return True
        testo = _(
            "⚠️ Ordine %(numero)s importato, ma il totale NON torna: in Odoo "
            "%(trovato).2f, su Cdiscount %(atteso).2f. I prezzi scritti sono "
            "quelli pagati dal cliente, IVA INCLUSA: se in Odoo e' piu' alto, "
            "l'aliquota della posizione fiscale non e' «IVA inclusa»; se e' "
            "diverso in altro modo, o l'aliquota non e' quella francese, o "
            "mancano le spese di spedizione.") % {
                "numero": letto["numero"], "trovato": trovato, "atteso": atteso}
        note.append(testo)
        self._registra("pull_orders", "error", testo,
                       external_id=letto["numero"])
        return False

    def _cliente_cdiscount(self, letto):
        """Il `res.partner` del compratore, per riferimento Cdiscount.

        ⚠️ Nella forma letta il cliente e' ANONIMO: ne' email ne' telefono.
        L'aggancio e' il suo `customer.reference`, scritto in `ref`. Un
        indirizzo che cambia da un ordine all'altro aggiorna il contatto.
        """
        Partner = self.env["res.partner"].sudo()
        dati = letto["fatturazione"]
        riferimento = ("CDISCOUNT:%s" % letto["cliente"]
                       if letto["cliente"] else "")
        esistente = Partner.browse()
        if riferimento:
            esistente = Partner.search(
                [("ref", "=", riferimento),
                 ("company_id", "in", [False, self.channel.company_id.id])],
                limit=1)
        valori = self._valori_indirizzo(dati)
        valori["name"] = (dati["azienda"] or dati["nome"]
                          or _("Cliente Cdiscount %s") % letto["numero"])
        if dati["azienda"]:
            valori["is_company"] = True
        if riferimento:
            valori["ref"] = riferimento
        if esistente:
            esistente.write({c: v for c, v in valori.items() if v})
            return esistente
        return Partner.create(valori)

    def _indirizzo_consegna(self, cliente, dati):
        """L'indirizzo di consegna, come contatto figlio del cliente."""
        Partner = self.env["res.partner"].sudo()
        valori = self._valori_indirizzo(dati)
        esistente = Partner.search(
            [("parent_id", "=", cliente.id), ("type", "=", "delivery"),
             ("street", "=", valori["street"]), ("zip", "=", valori["zip"]),
             ("city", "=", valori["city"])], limit=1)
        if esistente:
            return esistente
        valori.update({"parent_id": cliente.id, "type": "delivery",
                       "name": dati["nome"] or dati["azienda"] or cliente.name})
        return Partner.create(valori)

    def _valori_indirizzo(self, dati):
        paese = self.env["res.country"].sudo().search(
            [("code", "=", dati["paese"])], limit=1) if dati["paese"] else None
        return {
            "street": dati["via"] or False,
            "street2": dati["via2"] or False,
            "zip": dati["cap"] or False,
            "city": dati["citta"] or False,
            "country_id": paese.id if paese else False,
        }

    def _registra_righe_ordine(self, mappa, ordine, letto, prodotti):
        """Una `cdiscount.riga.ordine` per ogni riga di Octopia."""
        Riga = self.env["cdiscount.riga.ordine"].sudo()
        per_prodotto = {}
        for linea in ordine.order_line:
            per_prodotto.setdefault(linea.product_id.id, linea)
        for riga in letto["righe"]:
            prodotto = prodotti.get(riga["id"])
            linea = per_prodotto.get(prodotto.id) if prodotto else None
            Riga.create({
                "channel_id": self.channel.id,
                "order_map_id": mappa.id,
                "riga": riga["id"],
                "ordine": letto["numero"],
                "codice": riga["codice"],
                "gtin": riga["gtin"] or False,
                "sale_line_id": linea.id if linea else False,
                "stato_cdiscount": riga["stato"] or False,
                "quantita": riga["quantita"],
                "prezzo": riga["prezzo"],
                "spedizione": riga["spedizione"],
                "commissione_con_iva": riga["commissione_con_iva"],
                "commissione_senza_iva": riga["commissione_senza_iva"],
                "tasso_commissione": riga["tasso_commissione"],
                "promesso_entro": self._data_iso(riga["promesso_entro"]),
                "spedire_entro": self._data_iso(riga["spedire_entro"]),
            })

    # ------------------------------------------------------------------
    # La spedizione: UN collo per l'intero ordine
    # ------------------------------------------------------------------
    def _spedizione_ferma(self, esterno, motivo):
        """Scrive perche' non si e' comunicato niente, e si ferma. Sempre False."""
        self._registra("push_shipment", "error",
                       _("Spedizione dell'ordine %(ordine)s non comunicata: "
                         "%(motivo)s.") % {"ordine": esterno, "motivo": motivo},
                       external_id=esterno)
        return False

    def _corriere_cdiscount(self, trasferimenti, esterno):
        """`(codice, modello url)` del corriere per Cdiscount, o None dopo
        aver detto perche'. Tutti i trasferimenti devono avere lo stesso
        corriere: Cdiscount vuole UN collo per ordine."""
        # Lazy: il banco fuori da Odoo non ha questo modulo del tronco.
        from odoo.addons.integrations_core.connectors.carrier_resolver import (  # noqa: E501
            NON_TRADOTTO)
        codici = {}
        for trasferimento in trasferimenti:
            modello, id_sorgente, nome = (
                self.channel._picking_carrier_source(trasferimento))
            if not id_sorgente:
                self._spedizione_ferma(
                    esterno, _("il trasferimento %s non ha un vettore da cui "
                               "ricavare il corriere") % trasferimento.name)
                return None
            esito = self.env["centrivo.carrier.map"].resolve_external_code(
                self.channel, modello, id_sorgente, self.channel.company_id)
            if not esito:
                if esito.failure_reason == NON_TRADOTTO:
                    self._spedizione_ferma(
                        esterno,
                        _("il corriere «%s» non ha un nome per Cdiscount "
                          "(in Francia si spedisce solo con BRT e GLS). "
                          "Guarda in Corrieri → Copertura corrieri, oppure "
                          "aggiungi un'eccezione di canale.")
                        % (esito.brand_name or "?"))
                else:
                    self._spedizione_ferma(
                        esterno,
                        _("il vettore «%s» non e' collegato a nessun corriere. "
                          "Aggiungi la riga in Corrieri → Vettori.") % nome)
                return None
            codici[esito.external_code] = esito.tracking_url_template or ""
        if len(codici) > 1:
            self._spedizione_ferma(
                esterno, _("i colli di questo ordine viaggiano con corrieri "
                           "diversi (%s), e Cdiscount accetta UN collo per "
                           "ordine") % ", ".join(sorted(codici)))
            return None
        return next(iter(codici.items()))

    def push_shipment(self, order_map):
        """Comunica a Cdiscount che l'ordine e' partito: UN collo, UNA chiamata.

        ⚠️ Nessuna riga di questo metodo e' stata provata contro Cdiscount
        vero: la forma e' LETTA (`docs/cdiscount-ordini-contratto.md` §4).
        Per questo la spedizione nasce col CANCELLO CHIUSO: dall'automatismo
        non parte niente, dal pulsante sull'ordine si'. Il cancello si apre
        dalla scheda del canale dopo aver visto sul portale che il primo
        invio e' andato.

        ⚠️ L'esito incerto non e' un successo e non e' un fallimento: non si
        segna (si perderebbe la spedizione) e non si ritenta da soli (un
        collo dichiarato due volte e' un rifiuto o un doppione).
        """
        esterno = order_map.external_id
        canale = self.channel.sudo()
        if not (canale.cdiscount_spedizione_provata
                or self.env.context.get("cdiscount_spedizione_a_mano")):
            return self._spedizione_ferma(
                esterno, _("il primo invio a Cdiscount si fa A MANO, dal "
                           "pulsante sull'ordine. Quando sul portale l'ordine "
                           "risultera' spedito, premi «Ho verificato sul "
                           "portale» sulla scheda del canale, e da li' in poi "
                           "ci pensera' anche l'automatismo"))
        Riga = self.env["cdiscount.riga.ordine"].sudo()
        righe = Riga.search([("order_map_id", "=", order_map.id)])
        if order_map.state != "imported" or not order_map.sale_order_id:
            return self._spedizione_ferma(
                esterno, _("l'ordine non risulta importato, o non ha un "
                           "ordine di vendita collegato"))
        if not righe:
            return self._spedizione_ferma(
                esterno, _("non ci sono righe Cdiscount registrate per "
                           "questo ordine"))
        if all(righe.mapped("spedizione_comunicata")):
            self._registra("push_shipment", "skip",
                           _("Ordine %s: spedizione gia' comunicata.")
                           % esterno, external_id=esterno)
            if not order_map.shipment_pushed:
                order_map.sudo().shipment_pushed = True
            return True
        trasferimenti = order_map.sale_order_id.picking_ids.filtered(
            lambda p: p.state == "done"
            and (p.carrier_tracking_ref or "").strip())
        if not trasferimenti:
            return self._spedizione_ferma(
                esterno, _("nessuna spedizione pronta: serve un trasferimento "
                           "in stato «Fatto» con il numero di tracciamento"))
        numeri = list(dict.fromkeys(
            (t.carrier_tracking_ref or "").strip() for t in trasferimenti))
        if len(numeri) > 1:
            return self._spedizione_ferma(
                esterno, _("ci sono %s numeri di tracciamento diversi, e "
                           "Cdiscount accetta UN collo per ordine")
                % len(numeri))
        corriere = self._corriere_cdiscount(trasferimenti, esterno)
        if not corriere:
            return False
        codice, modello_url = corriere
        etichetta = etichetta_corriere(codice)
        if not etichetta:
            return self._spedizione_ferma(
                esterno, _("il codice corriere «%s» non e' fra i corrieri "
                           "di Cdiscount") % codice)
        try:
            corpo = corpo_spedizione(numeri[0], etichetta,
                                     url_tracciamento(modello_url, numeri[0]))
        except ValueError as errore:
            return self._spedizione_ferma(esterno, str(errore))

        client = self._client()
        risposta = client.chiama(
            "POST", "/orders/%s/shipments" % quote(esterno, safe=""), corpo)
        incerta = (self._causa_incerta(risposta)
                   or self._quota_esaurita(risposta))
        if incerta:
            self._registra("push_shipment", "error", _(
                "Ordine %(ordine)s: esito IGNOTO della spedizione "
                "(%(perche)s). Cdiscount puo' averla presa lo stesso: NON si "
                "ritenta da soli. Va guardato sul portale, e se risulta "
                "spedito si segna a mano.") % {"ordine": esterno,
                                               "perche": incerta},
                str(corpo)[:MAX_PAYLOAD], esterno)
            return False
        if not risposta.ok:
            self._registra("push_shipment", "error", _(
                "Ordine %(ordine)s: Cdiscount ha rifiutato la spedizione "
                "(%(motivo)s). Niente e' stato segnato: si corregge e si "
                "ripete.") % {"ordine": esterno,
                              "motivo": self._motivo_stato(
                                  risposta.stato, risposta.messaggio)},
                str(corpo)[:MAX_PAYLOAD], esterno)
            return False
        righe.write({"spedizione_comunicata": True})
        order_map.sudo().shipment_pushed = True
        self._registra("push_shipment", "success", _(
            "Ordine %(ordine)s: spedizione comunicata a Cdiscount — collo "
            "%(collo)s con %(corriere)s.") % {
                "ordine": esterno, "collo": numeri[0],
                "corriere": etichetta},
            str(corpo)[:MAX_PAYLOAD], esterno)
        return True
