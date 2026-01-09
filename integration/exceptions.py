# See LICENSE file for full copyright and licensing details.
import inspect

from odoo import _


class NotMappedFromExternal(Exception):

    def __init__(self, msg, model_name=None, code=None, integration=None):
        if model_name and code:
            msg = '%s → %s(code=%s, integration=%s) \n%s' % (integration.name, model_name, code, integration.id, msg)

        super(NotMappedFromExternal, self).__init__(msg)


class NotMappedToExternal(Exception):

    def __init__(self, msg, model_name=None, obj_id=None, integration=None):
        if model_name and obj_id and integration:
            msg = (
                f'Export error for model "{model_name}" with ID "{obj_id}" in {integration.name} '
                f'integration (ID: {integration.id}).\n'
                f'Details: {msg}\n\n'
                f'Suggested action: The corresponding external record does not exist. This usually means '
                f'the Odoo entity (e.g., product) was not exported to the e-commerce system.\n'
                f'Please try the following steps:\n'
                f'  1. Manually export the problematic entity from Odoo to the e-commerce system, or\n'
                f'  2. If there is an export job in the queue, please wait until the export job is completed.\n\n'
                f'If the issue persists or the export fails, please contact our support '
                f'team: https://support.ventor.tech/'
            )
        else:
            msg = (
                f'Export error: {msg}\n\n'
                f'Suggested action: The corresponding external record does not exist. Please manually export '
                f'the Odoo entity to '
                f'the e-commerce system or wait for the export job to complete. '
                f'If the issue persists, please contact our support team: https://support.ventor.tech/'
            )

        super(NotMappedToExternal, self).__init__(msg)


class NoReferenceFieldDefined(Exception):

    def __init__(self, msg, object_name=None):
        super(NoReferenceFieldDefined, self).__init__(msg)
        self.object_name = object_name


class ApiImportError(Exception):

    def __init__(self, msg):
        super(ApiImportError, self).__init__(msg)


class ApiExportError(Exception):

    def __init__(self, msg):
        super(ApiExportError, self).__init__(msg)


class NoExternal(Exception):

    def __init__(self, msg, model_name=None, code=None, integration=None):
        if model_name and code and integration:
            msg = (
                f'External record not found for model "{model_name}" with code "{code}" in '
                f'integration ID {integration.id}.\n'
                f'Details: {msg}\n\n'
                f'Suggested action: Please ensure the relevant objects are imported from the e-commerce system.\n'
                f'Steps to resolve:\n'
                f'  1. Check the e-commerce system to confirm that the record with code "{code}" exists.\n'
                f'  2. If the record exists, make sure it is correctly imported into Odoo via the import process.\n'
                f'  3. If the record does not exist, ensure it is created in the e-commerce system and '
                f'then import it into Odoo.\n\n'
                f'If the issue persists, please contact our support team: https://support.ventor.tech/'
            )
        else:
            msg = (
                f'External record not found: {msg}\n\n'
                f'Suggested action: Please ensure the relevant objects are imported from the e-commerce system.\n'
                f'Steps to resolve:\n'
                f'  1. Confirm the external record exists in the e-commerce system.\n'
                f'  2. Import the record into Odoo using the import process.\n\n'
                f'If the issue persists, please contact our support team: https://support.ventor.tech/'
            )

        super(NoExternal, self).__init__(msg)


class MultipleExternalRecordsFound(Exception):

    def __init__(self, msg, model_name=None, code=None, integration=None, duplicates=None):
        if model_name and code and integration:
            duplicate_info = ', '.join(str(dup.id) for dup in duplicates) if duplicates else 'unknown'
            msg = (
                f'Multiple external records found for model "{model_name}" with code "{code}" '
                f'in {integration.name} integration (ID: {integration.id}).\n'
                f'Details: {msg}\n'
                f'Duplicate record IDs: {duplicate_info}\n\n'
                f'Suggested action: Please remove duplicates in the external records.\n'
                f'Steps to resolve:\n'
                f'  1. Go to the external records for "{model_name}" model and search for records with code "{code}".\n'
                f'  2. Identify and remove the duplicated records to ensure only one unique record exists.\n'
                f'  3. Once the duplicates are resolved, restart any failed jobs or retry '
                f'the action you were attempting.\n\n'
                f'If the issue persists, please contact our support team: https://support.ventor.tech/'
            )
        else:
            msg = (
                f'Multiple external records found: {msg}\n\n'
                f'Suggested action: Please remove duplicates in the external records.\n'
                f'Steps to resolve:\n'
                f'  1. Search for the relevant records in the external system and resolve duplicates.\n'
                f'  2. Once the duplicates are resolved, restart any failed jobs or retry '
                f'the action you were attempting.\n\n'
                f'If the issue persists, please contact our support team: https://support.ventor.tech/'
            )

        super(MultipleExternalRecordsFound, self).__init__(msg)


class IntegrationNotImplementedError(NotImplementedError):
    def __init__(self, msg: str = ''):
        method_name = inspect.stack()[1].function
        msg = (
            _(
                'Method %s must be implemented by each connector. '
                'Additional information: %s\n\n'
                'This is a technical issue that cannot be fixed through configuration and requires '
                'investigation by our developers. '
                'If you encounter this error, please contact our support team: https://support.ventor.tech/'
            ) % (method_name, msg)
        )

        super(IntegrationNotImplementedError, self).__init__(msg)
