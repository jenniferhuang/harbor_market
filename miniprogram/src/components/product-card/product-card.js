Component({
  properties: {
    item: {
      type: Object,
      value: {},
    },
  },

  methods: {
    selectItem() {
      this.triggerEvent('select', { productCode: this.properties.item.productCode })
    },
  },
})
