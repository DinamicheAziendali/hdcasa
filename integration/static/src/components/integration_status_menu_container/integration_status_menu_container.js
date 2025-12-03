/** @odoo-module **/

import { registry } from '@web/core/registry';
import { useService } from "@web/core/utils/hooks";
import { getMessagingComponent } from "@mail/utils/messaging_component";
import { registerModel } from '@mail/model/model_core';
import { attr, many, one } from '@mail/model/model_field';

import '@integration/components/integration_status_menu/integration_status_menu';

const { Component } = owl;

registerModel({
    name: 'IntegrationStatusMenuModel',
    lifecycleHooks: {
        _created() {
            this._fetchData();
            document.addEventListener('click', this._onClickGlobal, true);
        },
        _willDelete() {
            document.removeEventListener('click', this._onClickGlobal, true);
        },
    },
    recordMethods: {
        /**
         * Close the messaging menu. Should reset its internal state.
         */
        close() {
            this.update({ isOpen: false });
        },
        /**
         * @param {MouseEvent} ev
         */
        onClickToggler(ev) {
            // avoid following dummy href
            ev.preventDefault();
            if (!this.exists()) {
                return;
            }
            this.toggleOpen();
        },
        /**
         * Toggle whether the messaging menu is open or not.
         */
        toggleOpen() {
            this.update({ isOpen: !this.isOpen });
            this._fetchData();
        },
        /**
         * Closes the menu when clicking outside, if appropriate.
         *
         * @private
         * @param {MouseEvent} ev
         */
        _onClickGlobal(ev) {
            if (!this.exists()) {
                return;
            }
            if (!this.component) {
                return;
            }
            // ignore click inside the menu
            if (!this.component.root.el || this.component.root.el.contains(ev.target)) {
                return;
            }
            // in all other cases: close the messaging menu when clicking outside
            this.close();
        },
        async _fetchData() {
            const integrations = await this.messaging.rpc({
                model: 'sale.integration',
                method: 'systray_get_integrations',
                args: [],
                kwargs: {},
            })

            const activityCounterFailed = _.reduce(integrations, function (total_count, p_data) {return total_count + p_data.failed_jobs_count || 0;}, 0);
            const activityCounterMissing = _.reduce(integrations, function (total_count, p_data) { return total_count + p_data.missing_mappings_count || 0; }, 0);
            const activityCounter = activityCounterFailed + ' / ' + activityCounterMissing;
            return Promise.all(
                    [integrations, activityCounterFailed, activityCounterMissing, activityCounter]
                ).then(this._loadedCallback.bind(this));
        },

        _loadedCallback([integrations, activityCounterFailed, activityCounterMissing, activityCounter]) {
            this.update({
                integrations: integrations,
                activityCounterFailed: activityCounterFailed,
                activityCounterMissing: activityCounterMissing,
                activityCounter: activityCounter,
            });
        },
    },
    fields: {
        integrations: attr({ default: [] }),
        activityCounterFailed: attr(),
        activityCounterMissing: attr(),
        activityCounter: attr(),
        component: attr(),
        isOpen: attr({
            default: false,
        }),
        viewId: attr({
            compute() {
                return _.uniqueId('o_integrationStatusMenu_');
            },
        }),
    },
});

export class IntegrationStatusMenuContainer extends Component {

    /**
     * @override
     */
    async setup() {
        super.setup();

        this.messaging = useService("messaging");
        this.messaging.get().then((messaging) => {
            this.integrationStatusMenu = messaging.models['IntegrationStatusMenuModel'].insert();
            this.render();
        });
    }
}

Object.assign(IntegrationStatusMenuContainer, {
    components: { IntegrationStatusMenu: getMessagingComponent('IntegrationStatusMenu') },
    template: 'integration.IntegrationStatusMenuContainer',
});

registry.category('systray').add('integration.IntegrationStatusMenuContainer', {
    Component: IntegrationStatusMenuContainer,
});
