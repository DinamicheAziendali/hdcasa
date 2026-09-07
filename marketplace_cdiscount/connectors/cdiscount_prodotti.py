# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""I prodotti che su Cdiscount ci sono GIA', e come si leggono.

Solo forma: qui non si parla con nessuno e non si importa Odoo.

⚠️ **Perche' questo file esiste.** Fino al 2026-09-02 il modulo credeva di
essere lui a far nascere le schede. Il primo caricamento vero ha detto
un'altra cosa: il catalogo Octopia e' **condiviso**, «under a single product
sheet per GTIN», e 88 righe su 176 sono tornate con «Vous n'etes pas
autorises a modifier ce champ car vous n'en etes pas le createur». Di quei
176 prodotti, **76 erano gia' vendibili** senza che noi avessimo mandato
niente.

Da li' la decisione di Angelo: il modulo non crea inserzioni, le **aggancia**
— esattamente come `kaufland.riaggancia`, e per la stessa ragione scritta
nella sua docstring: «creare offerte prima di sapere quali esistono significa
duplicarle su un marketplace vero».

Cosa e' MISURATO e cosa e' LETTO:

- **LETTO** (documentazione ufficiale, «Retrieve seller Products»): che
  `GET /products` renda i prodotti creati **o arricchiti** dal venditore, che
  pagini **a cursore** (`cursor` + `limit`), che ogni riga porti `gtin`,
  `sellerProductReference`, `productReference`, `permissions` e
  `isMarketable`, e che `pageIndex`/`pageSize` siano **deprecati** su questo
  endpoint.
- **MISURATO** (rapporti di integrazione del 2026-09-02): che il riferimento
  di catalogo abbia la forma `AUC<gtin>`, e che la colonna «Je peux vendre»
  del rapporto corrisponda a un prodotto che possiamo offrire.

⚠️ **Il rapporto pagina a INDICE, questo endpoint a CURSORE.** Sono due cose
diverse e non si copia la seconda sulla prima: `pageIndex` qui non farebbe
errore, andrebbe semplicemente ignorato, e si riceverebbe sempre la stessa
pagina — cioe' un riaggancio che gira per sempre sui primi cento prodotti.
"""
from urllib.parse import quote

# Quanti prodotti per pagina. ⚠️ Il parametro si chiama `limit`, non
# `pageSize`: vedi l'avvertenza in testa al file.
PRODOTTI_PER_PAGINA = 100

# ⚠️ Il tetto non e' pessimismo: un cursore che tornasse sempre uguale — per
# un difetto loro o per una risposta storta — farebbe girare il ciclo per
# sempre. Un riaggancio che non finisce mai e' peggio di uno che si ferma
# dicendo perche'. Ventimila prodotti sono due ordini di grandezza sopra il
# perimetro di oggi: se lo si tocca, la notizia e' che qualcosa non va.
MAX_PAGINE_PRODOTTI = 200

# L'indirizzo dell'elenco. Vive qui e non in `cdiscount.py` perche' e' questo
# il file che sa come si legge quella risposta.
API_PRODOTTI = "/products"


class LetturaInterrotta(Exception):
    """L'elenco dei prodotti non si e' potuto leggere fino in fondo.

    ⚠️ E' un'eccezione e non un valore di ritorno **apposta**: chi la riceve
    non deve poter proseguire per distrazione. Agganciare su un elenco
    parziale vuol dire dichiarare «questi sono i prodotti che esistono»
    avendone visti la meta', e le offerte che nascono da li' sono offerte su
    un catalogo immaginario.
    """

    def __init__(self, risposta=None, messaggio=""):
        self.risposta = risposta
        self.messaggio = messaggio or self._dal_responso(risposta)
        super().__init__(self.messaggio)

    @staticmethod
    def _dal_responso(risposta):
        if risposta is None:
            return "la lettura dei prodotti si e' interrotta"
        stato = getattr(risposta, "stato", "?")
        motivo = getattr(risposta, "messaggio", "") or "nessun dettaglio"
        return ("Cdiscount ha risposto %s alla lettura dei prodotti: %s"
                % (stato, motivo))


def _testo(valore):
    """Il valore come testo pulito, qualunque cosa sia arrivata dal JSON.

    ⚠️ `str()` PRIMA di `.strip()`: `sellerProductReference` arriva da un
    JSON e non e' garantito testo. Un numero farebbe esplodere il giro con un
    AttributeError a meta' elenco, e le righe gia' agganciate resterebbero a
    meta'. E' la stessa cautela di `kaufland.riaggancia` su `id_offer`.
    """
    if valore is None:
        return ""
    return str(valore).strip()


def pagine_prodotti(client, limite=PRODOTTI_PER_PAGINA):
    """Le pagine di `GET /products`, seguendo il cursore fino alla fine.

    Rende una lista di righe per volta. Solleva `LetturaInterrotta` se la
    lettura non arriva in fondo: vedi la ragione nella sua docstring.
    """
    cursore = None
    for _giro in range(MAX_PAGINE_PRODOTTI):
        percorso = "%s?limit=%d" % (API_PRODOTTI, limite)
        if cursore:
            # ⚠️ In escape: un cursore con `&` o `=` dentro spezzerebbe la
            # query, e la pagina dopo tornerebbe sbagliata senza un errore.
            percorso += "&cursor=%s" % quote(_testo(cursore), safe="")
        risposta = client.chiama("GET", percorso)
        if not getattr(risposta, "ok", False):
            raise LetturaInterrotta(risposta)
        corpo = risposta.corpo if isinstance(risposta.corpo, dict) else {}
        righe = corpo.get("items") or []
        if righe:
            yield righe
        cursore = corpo.get("cursor")
        if not cursore:
            return
    raise LetturaInterrotta(messaggio=(
        "l'elenco dei prodotti non e' finito dopo %d pagine e il cursore "
        "continua a tornare: non si aggancia niente su una lettura che non "
        "si chiude" % MAX_PAGINE_PRODOTTI))


def leggi_prodotto(riga, seller_id=""):
    """Di una riga di `GET /products`: `(codice, riferimento, vendibile, gtin)`.

    - `codice` e' il **nostro** SKU, la chiave con cui si accoppia a
      `product.product.default_code`;
    - `riferimento` e' il codice della scheda nel catalogo Octopia;
    - `vendibile` dice se possiamo metterci un'offerta;
    - `gtin` e' il codice a barre, che in Odoo sta su `product.product.barcode`.

    ⚠️ **IL NOSTRO CODICE STA ANNIDATO IN `sellers[]`, NON AL PRIMO LIVELLO.**
    E' l'errore che il 2026-09-03 ha fatto scartare **70 righe su 70** al
    primo riaggancio vero: si leggeva `sellerProductReference`, che nella
    risposta di `GET /products` non esiste. La forma e':

        {"reference": "A58BC78",          <- il riferimento Octopia
         "gtin": "8806088879093",
         "isMarketable": true,
         "sellers": [{"reference": "424639",           <- il seller id
                      "productReference": "HDC00001",  <- il NOSTRO codice
                      "isCreator": true}]}

    ⚠️ E `sellers[]` puo' portare PIU' venditori, perche' il catalogo e'
    condiviso: si prende la riga del **nostro** seller id, non la prima che
    capita. Prendere la prima vorrebbe dire agganciare il codice interno di
    un concorrente al nostro prodotto.

    ⚠️ `gtin` esce di qui perche' per i prodotti creati da ALTRI la
    documentazione avverte che «some fields will not be displayed»:
    `sellers[]` puo' mancare del tutto. Il GTIN invece c'e' sempre, ed e'
    l'unico aggancio possibile per quei prodotti — che nel nostro caso sono
    la maggioranza (76 su 176 al primo caricamento).

    ⚠️ `isMarketable` ASSENTE vale «vendibile», non «no». Trattare l'assenza
    come un rifiuto farebbe sparire in silenzio **tutti** i prodotti il
    giorno che Octopia smettesse di mandarlo — e un riaggancio che aggancia
    zero righe senza lamentarsi e' il guasto peggiore di tutti.
    """
    if not isinstance(riga, dict):
        return ("", "", True, "")
    codice = ""
    venditori = riga.get("sellers")
    if isinstance(venditori, list):
        for venditore in venditori:
            if not isinstance(venditore, dict):
                continue
            if seller_id and _testo(venditore.get("reference")) != _testo(seller_id):
                continue
            codice = _testo(venditore.get("productReference"))
            if codice:
                break
    return (codice,
            _testo(riga.get("reference")),
            bool(riga.get("isMarketable", True)),
            _testo(riga.get("gtin")))


# I secchi in cui puo' finire una riga letta. Cambiarne uno qui e non in
# `riaggancia()` fa fallire `secchi_tornano`, che e' esattamente il punto.
SECCHI = ("agganciate", "senza_prodotto", "non_vendibili", "contese",
          "scartate")


def secchi_tornano(esito):
    """Vero se ogni riga letta e' finita in uno e un solo secchio.

    ⚠️ E' la guardia che rende impossibile un riaggancio «riuscito» a zero
    agganci. Se Octopia rinominasse `sellerProductReference`, ogni riga
    cadrebbe negli scarti: il conto tornerebbe lo stesso, ma `agganciate` a
    zero su `lette` a mille e' una notizia che si legge a colpo d'occhio.
    Quello che questa guardia impedisce e' il caso peggiore — righe che non
    finiscono da nessuna parte e spariscono senza lasciare traccia.
    """
    return sum(esito.get(nome, 0) for nome in SECCHI) == esito.get("lette", 0)
