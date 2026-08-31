# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
"""Com'e' fatta un'offerta Kaufland. Solo forma: qui non si parla con nessuno.

⚠️ Le due strade — l'offerta singola e l'aggiornamento in blocco — si difendono
allo stesso modo, con gli stessi controlli e gli stessi messaggi. La seconda
gira su migliaia di offerte gia' vive: e' l'ultimo posto dove ci si puo'
permettere una difesa piu' debole.

Questo file NON importa Odoo: si prova con tools/test_kaufland_offerte.py.
"""
from decimal import ROUND_HALF_UP, Decimal

# Il tetto dichiarato da Kaufland.
QUANTITA_MASSIMA = 99999
# Nuovo. Non vendiamo usato: se un giorno accadra' sara' una scelta
# dichiarata, non un valore predefinito cambiato di nascosto.
CONDIZIONE = "NEW"
# ⚠️ Il tetto di Kaufland per un aggiornamento in blocco. Una richiesta con
# 151 unita' viene rifiutata INTERA, non parzialmente: sforare significa
# perdere un giro completo senza capire perche'.
GRUPPO = 150


def in_centesimi(euro):
    """Da euro a centesimi interi, arrotondando.

    ⚠️ Non si tronca. I numeri a virgola non sono esatti: 8.7 * 100 fa
    869.9999..., e troncare darebbe 869 — un centesimo in meno del prezzo che
    qualcuno ha deciso, moltiplicato per migliaia di offerte.
    """
    return int(Decimal(str(euro)).scaleb(2).quantize(Decimal("1"),
                                                     rounding=ROUND_HALF_UP))


def _testo(valore):
    """Il valore come testo, perche' i codici Kaufland somigliano a numeri.

    ⚠️ `"195839"` e' un gruppo di spedizione, non una quantita': un campo che
    consegna l'intero 195839 e' piu' che plausibile. Chiamare `.strip()` su un
    intero darebbe AttributeError, e chi intercetta ValueError non lo vedrebbe
    passare.

    ⚠️ `False` vale come assente, esattamente come `None`: in Odoo un campo
    `Char` non valorizzato si legge `False`, e senza questa guardia il canale
    col magazzino lasciato vuoto spedirebbe a Kaufland un magazzino
    inesistente chiamato «False» su tutte le offerte.

    ⚠️ Il confronto e' sull'identita' (`is False`) e non sull'uguaglianza,
    perche' in Python `0 == False`: lo zero invece e' un identificativo buono
    e deve arrivare a Kaufland come `"0"`.
    """
    if valore is None or valore is False:
        return ""
    return str(valore)


def _prezzo_valido(prezzo_centesimi, chi):
    """Il prezzo in centesimi, o un ValueError che dice cosa non va e a chi.

    ⚠️ Un intero, non un numero a virgola. `in_centesimi` arrotonda bene, ma
    niente obbliga a passarci: chi scrive `prezzo * 100` perde il centesimo
    (8.7 * 100 fa 869.9999..., e 0.4 diventerebbe 0 pur superando il controllo
    del prezzo positivo). Qui non convertiamo al posto di chi chiama: chi ha
    gia' convertito farebbe una conversione doppia. Si rifiuta e si dice come.
    """
    if prezzo_centesimi is None:
        raise ValueError(
            "%s: il prezzo non c'e'. Un prezzo mancante non e' un'offerta."
            % chi)
    if isinstance(prezzo_centesimi, bool) or not isinstance(prezzo_centesimi,
                                                            int):
        raise ValueError(
            "%s: il prezzo %r non e' un numero intero di centesimi. Se hai "
            "degli euro convertili con in_centesimi(), che arrotonda: "
            "moltiplicare per 100 e troncare perde un centesimo."
            % (chi, prezzo_centesimi))
    if prezzo_centesimi <= 0:
        raise ValueError(
            "%s: il prezzo e' %r. Un prezzo a zero o negativo e' un dato "
            "mancante, non un'offerta. (La quantita' a zero invece e' "
            "legittima: e' cosi' che si sospende un'offerta senza "
            "cancellarla.)" % (chi, prezzo_centesimi))
    return prezzo_centesimi


def _pezzi(quantita):
    """La quantita' riportata dentro i limiti che Kaufland accetta."""
    return max(0, min(int(quantita or 0), QUANTITA_MASSIMA))


def corpo_offerta(ean, prezzo_centesimi, quantita, id_offer,
                  id_gruppo_spedizione, giorni_consegna, id_magazzino=None):
    """Il corpo da mandare a `POST /units/`.

    ⚠️ Il mercato NON sta qui dentro. Kaufland vuole `storefront` nella
    stringa di ricerca dell'indirizzo, e se lo trova nel corpo rifiuta
    l'offerta con due errori che sembrano contraddirsi: «proprieta' in
    eccesso, non ammessa» e «obbligatorio». Misurato sul vero il 2026-08-25,
    su 166 offerte rifiutate su 166.

    ⚠️ NON contiene `minimum_price`, e non deve contenerlo mai per sbaglio:
    quel campo da' a Kaufland licenza di abbassare il prezzo da solo fino a
    quella soglia.
    """
    prezzo = _prezzo_valido(prezzo_centesimi, "Offerta %s" % id_offer)
    gruppo = _testo(id_gruppo_spedizione)
    if not gruppo.strip():
        raise ValueError(
            "Offerta %s: manca il gruppo di spedizione, e Kaufland rifiuta le "
            "offerte che non ne hanno uno." % id_offer)

    corpo = {
        "ean": ean,
        "condition": CONDIZIONE,
        "listing_price": prezzo,
        "amount": _pezzi(quantita),
        "id_offer": id_offer,
        "id_shipping_group": gruppo,
        "handling_time": int(giorni_consegna or 0),
    }
    magazzino = _testo(id_magazzino)
    if magazzino.strip():
        # Senza, Kaufland usa il magazzino predefinito. Mandare una stringa
        # vuota non e' la stessa cosa che non mandare il campo.
        corpo["id_warehouse"] = magazzino
    return corpo


def _identificativo_unita(grezzo, posizione):
    """L'identificativo dell'unita' come numero, o un errore che dice quale.

    ⚠️ Una riga sporca fa cadere l'intera richiesta, non la sola riga: su 150
    cambi, un «invalid literal for int()» senza indizi costringe a cercare
    l'ago nel pagliaio mentre il giro e' gia' perso.
    """
    try:
        return int(grezzo)
    except (TypeError, ValueError):
        raise ValueError(
            "Cambio in posizione %d (la prima e' 0): l'identificativo "
            "dell'unita' e' %r, e non e' un numero. Kaufland rifiuta l'intera "
            "richiesta, non la sola riga sbagliata." % (posizione, grezzo))


def corpi_aggiornamento(cambi):
    """I corpi per `POST /units/bulk`, gia' divisi in gruppi da 150.

    Ogni cambio e' `{"id_unit": str, "prezzo_centesimi": int?, "quantita": int?}`.
    Si manda SOLO cio' che e' indicato: un aggiornamento che non cambia niente
    e' una chiamata sprecata, e su migliaia di offerte si sente.

    ⚠️ Nessun doppione dentro un gruppo: Kaufland rifiuta l'intera richiesta se
    la stessa unita' compare due volte. Se compare piu' volte si fonde campo
    per campo, e per ogni campo vince l'ultimo valore.
    """
    per_unita = {}
    for posizione, cambio in enumerate(cambi):
        id_unit = _identificativo_unita(cambio.get("id_unit"), posizione)
        dati = {}
        if cambio.get("prezzo_centesimi") is not None:
            dati["listing_price"] = _prezzo_valido(
                cambio["prezzo_centesimi"], "Cambio dell'unita' %s" % id_unit)
        if cambio.get("quantita") is not None:
            dati["amount"] = _pezzi(cambio["quantita"])
        if not dati:
            continue
        # ⚠️ Si fonde campo per campo, non record per record: se la lista nasce
        # da due sorgenti — un giro prezzi e un giro giacenze — sostituire il
        # record intero farebbe sparire in silenzio il campo dell'altra.
        per_unita.setdefault(id_unit, {}).update(dati)

    voci = [{"id_unit": id_unit, "unit_data": dati}
            for id_unit, dati in per_unita.items()]
    return [voci[i:i + GRUPPO] for i in range(0, len(voci), GRUPPO)]
