/** @odoo-module **/

import { registry } from '@web/core/registry';
import { Component, onWillStart, useState, xml } from '@odoo/owl';

const DENSITY = {
  152: '6dpmm',
  203: '8dpmm',
  300: '12dpmm',
  600: '24dpmm',
};

export class LabelPreviewField extends Component {
  static template = xml`
    <div class="zld-preview-container">
      <t t-if="imageSrc.value">
        <img
          t-att-src="imageSrc.value"
          t-att-alt="alt"
          t-att-title="title"
          t-att-class="className"
          t-att-style="style"
        />
      </t>
      <t t-else="">
        <span>Loading...</span>
      </t>
    </div>
  `

  imageSrc = useState({ value: null });

  setup() {
    onWillStart(async () => {
      const record = this.props.record;

      const dpmm = DENSITY[record.data.dpi];
      const width = record.data.width;
      const height = record.data.height;

      const formData = new FormData();
      formData.append('file', this.props.value);

      fetch(this.generateLabelaryUrl(dpmm, width, height), { method: 'POST', body: formData })
        .then((response) => response.blob())
        .then((blob) => {
          const previewURL = URL.createObjectURL(blob);

          // Update image source
          this.imageSrc.value = previewURL;
        });
    });
  }

  generateLabelaryUrl(dpmm, width, height) {
    return `https://api.labelary.com/v1/printers/${dpmm}/labels/${width}x${height}/0/`;
  }

}

registry.category('fields').add('zld_label_preview', LabelPreviewField);
