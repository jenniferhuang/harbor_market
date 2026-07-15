Component({
  properties: {
    value: {
      type: Number,
      value: 1,
    },
    min: {
      type: Number,
      value: 1,
    },
    max: {
      type: Number,
      value: 999,
    },
    disabled: {
      type: Boolean,
      value: false,
    },
  },

  methods: {
    decrement() {
      this.emitValue(this.properties.value - 1)
    },

    increment() {
      this.emitValue(this.properties.value + 1)
    },

    emitValue(nextValue) {
      if (this.properties.disabled) return
      const boundedValue = Math.max(
        this.properties.min,
        Math.min(this.properties.max, nextValue),
      )
      if (boundedValue !== this.properties.value) {
        this.triggerEvent('change', { value: boundedValue })
      }
    },
  },
})
