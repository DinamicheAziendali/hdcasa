# See LICENSE file for full copyright and licensing details.

from . import patch
from . import models
from . import wizard
from . import controllers


def post_init_hook(env):
    """ Generate API key for the installed integration. """
    Config = env['res.config.settings']
    Config.generate_integration_api_key()
