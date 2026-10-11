'use strict'

function validTimestamp(value) {
  return typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
}

function money(value) {
  return Number.isSafeInteger(value) && value >= 0
}

function evaluateCoupons(coupons, subtotalCents, now = Date.now()) {
  if (!money(subtotalCents) || !Number.isFinite(now)) throw new RangeError('优惠券计算金额或时间无效。')
  if (!Array.isArray(coupons)) return []
  return coupons.map((coupon) => {
    const source = coupon && typeof coupon === 'object' ? coupon : {}
    const id = source.coupon_id ?? source.id
    const validMoney = money(source.min_spend_cents) && money(source.discount_cents) && source.discount_cents > 0
    const shortfallCents = validMoney ? Math.max(0, source.min_spend_cents - subtotalCents) : 0
    let reason = 'eligible'
    if (!Number.isSafeInteger(id) || id < 1 || !validMoney ||
        (source.starts_at != null && !validTimestamp(source.starts_at)) ||
        (source.expires_at != null && !validTimestamp(source.expires_at))) reason = 'invalid'
    else if (source.is_active === false) reason = 'inactive'
    else if (source.starts_at != null && Date.parse(source.starts_at) > now) reason = 'not_started'
    else if (source.expires_at != null && Date.parse(source.expires_at) <= now) reason = 'expired'
    else if (!validTimestamp(source.claimed_at) || Date.parse(source.claimed_at) > now) reason = 'unclaimed'
    else if (shortfallCents > 0) reason = 'below_minimum'
    const eligible = reason === 'eligible'
    return {
      ...source, coupon_id: id, eligible, shortfallCents,
      appliedDiscountCents: eligible ? Math.min(source.discount_cents, subtotalCents) : 0,
      reason,
    }
  })
}

function bestCoupon(evaluations) {
  if (!Array.isArray(evaluations)) return null
  return evaluations.filter((item) => item && item.eligible === true &&
    money(item.appliedDiscountCents) && item.appliedDiscountCents > 0 &&
    Number.isSafeInteger(item.coupon_id) && item.coupon_id > 0)
    .reduce((best, item) => !best || item.appliedDiscountCents > best.appliedDiscountCents ||
      (item.appliedDiscountCents === best.appliedDiscountCents && item.coupon_id < best.coupon_id)
      ? item : best, null)
}

module.exports = { bestCoupon, evaluateCoupons }
