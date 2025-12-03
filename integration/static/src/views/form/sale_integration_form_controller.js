/** @odoo-module **/

import { registry } from '@web/core/registry';
import { formView } from '@web/views/form/form_view';
import { FormControlPanel } from "@web/views/form/control_panel/form_control_panel";

class SaleIntegrationFormControlPanel extends FormControlPanel {}

SaleIntegrationFormControlPanel.template = 'integration.SaleIntegrationFormControlPanel';

registry.category('views').add('sale_integration_form_view', {
    ...formView,
    ControlPanel: SaleIntegrationFormControlPanel,
});
