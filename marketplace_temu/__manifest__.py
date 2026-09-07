# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Connettore Temu (Famiglia A — sotto integrations_core).
#
# ⚠️ COSA FA, OGGI. Non è più «sola lettura»: il modulo SCRIVE verso Temu.
# Aggancia le schede che Temu ha già ai prodotti Odoo, tiene allineati prezzo
# e giacenza, scarica gli ordini e li traduce in ordini Odoo, comunica le
# spedizioni con corriere e tracking, invia le fatture.
#
# ⚠️ COSA NON FA, ED È UNA SCELTA. Non pubblica schede nuove, non corregge
# quelle esistenti, non carica immagini né testi: le schede le crea
# l'interfaccia Temu da file, questo modulo le aggancia e le tiene allineate.
#
# ⛔ I DUE BLOCCHI CHE OGGI FERMANO TUTTO, e non si risolvono scrivendo codice.
# 1) L'INDIRIZZO IP DI ODOO.SH NON È MAI STATO DICHIARATO A TEMU. Temu accetta
#    chiamate solo dagli IP dichiarati, e oggi c'è solo quello del server
#    Hetzner: da Odoo.sh non passa nemmeno una chiamata, permesso o no.
# 2) IL PERMESSO SUGLI IMPORTI ORDINE È NEGATO (3000032), e ferma OGNI import
#    di ordini: senza gli importi non si sa quanto ha pagato il cliente, e non
#    entra un solo ordine. Il ticket è già aperto (Seller Center → Assistenza)
#    e deve essere Temu a modificare la registrazione dell'app.
# Dettaglio e prove: docs/temu-cosa-gli-manca.md. Il bottone «Verifica token»
# sulla scheda del canale dice in ogni momento quali chiamate sono scoperte.
{
    "name": "Marketplace - Temu",
    "version": "18.0.8.0.0",
    "license": "OPL-1",
    "category": "Connector",
    "summary": "Connettore Temu (Open API EU): aggancio delle schede esistenti, "
               "allineamento di prezzo e giacenza, ordini, spedizioni e "
               "fatture. Non pubblica schede nuove. ATTENZIONE: gli ordini non "
               "entrano finché Temu non concede il permesso sugli importi, e "
               "da Odoo.sh non passa nessuna chiamata finché il suo IP non è "
               "dichiarato a Temu.",
    "author": "Angelo Margarella",
    "website": "https://www.hdcasa.it",
    "depends": ["integrations_core", "account"],
    # ⚠️ L'ordine. Prima «chi può fare cosa» (i permessi), poi «su quali
    # righe» (le regole multi-azienda): si leggono come le due metà della
    # stessa risposta, ed è la prima domanda da fare a un modulo che scrive su
    # un catalogo pubblico. Poi i dati, poi le viste — le viste per ultime
    # perché nominano modelli e azioni, non il contrario.
    "data": [
        "security/ir.model.access.csv",
        "views/temu_menu_root.xml",
        "security/temu_security.xml",
        "data/ir_cron.xml",
        "views/temu_listing_views.xml",
        "views/temu_shipment_views.xml",
        "views/temu_channel_views.xml",
        "views/temu_order_map_views.xml",
    ],
    "installable": True,
    "application": False,
}
