# -*- coding: utf-8 -*-
# Copyright 2026 Angelo Margarella (www.hdcasa.it)
# License OPL-1 (Odoo Proprietary License v1.0). See LICENSE file for full terms.
# Contratti astratti dei connettori e dei trasporti.
# NB: questi NON sono modelli Odoo: sono classi Python pure usate dai connettori
# concreti (es. marketplace_bricobravo). Vengono importate qui per registrare la
# classe base e i trasporti all'avvio del modulo.
from . import base
from . import transport
