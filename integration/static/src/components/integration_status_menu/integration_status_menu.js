/** @odoo-module **/

import { useComponentToModel } from '@mail/component_hooks/use_component_to_model';
import { registerMessagingComponent } from '@mail/utils/messaging_component';

const { Component } = owl;

export class IntegrationStatusMenu extends Component {

    /**
     * @override
     */
    setup() {
        super.setup();
        useComponentToModel({ fieldName: 'component' });
    }

    //--------------------------------------------------------------------------
    // Public
    //--------------------------------------------------------------------------

    /**
     * @returns {IntegrationStatusMenu}
     */
    get integrationStatusMenu() {
        return this.props.record;
    }

}

Object.assign(IntegrationStatusMenu, {
    props: { record: Object },
    template: 'integration.IntegrationStatusMenu',
});

registerMessagingComponent(IntegrationStatusMenu);
