'use strict'

let pendingCategory = null

function selectCatalogCategory(code) {
  if (typeof code !== 'string' || code.trim().length > 64 || [...code].some((character) => {
    const value = character.codePointAt(0)
    return value < 32 || value === 127
  })) {
    pendingCategory = null
    return
  }
  pendingCategory = code.trim()
}

function consumeCatalogCategory() {
  const category = pendingCategory
  pendingCategory = null
  return category
}

module.exports = { selectCatalogCategory, consumeCatalogCategory }
