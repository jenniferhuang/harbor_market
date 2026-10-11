'use strict'

const { bestCoupon, evaluateCoupons } = require('../src/domain/coupons')
const NOW = Date.parse('2026-10-11T08:00:00Z')
const OWNED = {
  coupon_id: 3, title: '店内满减券', min_spend_cents: 10000, discount_cents: 2000,
  starts_at: '2026-10-01T00:00:00Z', expires_at: '2026-10-31T16:00:00Z',
  is_active: true, claimed_at: '2026-10-10T00:00:00Z',
}

describe('owned coupon estimates', () => {
  it('shows the exact shortfall and applies a coupon at the threshold', () => {
    expect(evaluateCoupons([OWNED], 9999, NOW)[0]).toMatchObject({
      eligible: false, shortfallCents: 1, appliedDiscountCents: 0, reason: 'below_minimum',
    })
    expect(evaluateCoupons([OWNED], 10000, NOW)[0]).toMatchObject({
      eligible: true, shortfallCents: 0, appliedDiscountCents: 2000, reason: 'eligible',
    })
  })

  it('computes public offer thresholds without granting an unclaimed discount', () => {
    const offer = { ...OWNED, id: 9, coupon_id: undefined, claimed_at: undefined }
    expect(evaluateCoupons([offer], 9000, NOW)[0]).toMatchObject({
      coupon_id: 9, eligible: false, shortfallCents: 1000, appliedDiscountCents: 0, reason: 'unclaimed',
    })
    expect(bestCoupon(evaluateCoupons([offer], 12000, NOW))).toBeNull()
  })

  it.each([
    [{ is_active: false }, 'inactive'],
    [{ starts_at: '2026-10-11T08:00:00.001Z' }, 'not_started'],
    [{ expires_at: '2026-10-11T08:00:00Z' }, 'expired'],
    [{ claimed_at: null }, 'unclaimed'],
    [{ claimed_at: '2026-10-11T08:00:00.001Z' }, 'unclaimed'],
    [{ expires_at: 'invalid' }, 'invalid'],
    [{ min_spend_cents: '10000' }, 'invalid'],
    [{ discount_cents: -1 }, 'invalid'],
  ])('excludes invalid or unavailable claims (%s)', (changes, reason) => {
    expect(evaluateCoupons([{ ...OWNED, ...changes }], 20000, NOW)[0]).toMatchObject({
      eligible: false, appliedDiscountCents: 0, reason,
    })
  })

  it('caps discounts at the subtotal, never produces a negative total, and uses stable ID ties', () => {
    const coupons = [
      { ...OWNED, coupon_id: 8, min_spend_cents: 0, discount_cents: 2000 },
      { ...OWNED, coupon_id: 2, min_spend_cents: 0, discount_cents: 3000 },
      { ...OWNED, coupon_id: 1, min_spend_cents: 2000, discount_cents: 4000 },
    ]
    const evaluations = evaluateCoupons(coupons, 1000, NOW)
    expect(evaluations.map((item) => item.appliedDiscountCents)).toEqual([1000, 1000, 0])
    expect(bestCoupon(evaluations).coupon_id).toBe(2)
    expect(bestCoupon(evaluations.reverse()).coupon_id).toBe(2)
    expect(bestCoupon(evaluateCoupons(coupons, 0, NOW))).toBeNull()
  })

  it.each([-1, 1.5, '100', NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])('rejects unsafe subtotals (%s)', (subtotal) => {
    expect(() => evaluateCoupons([OWNED], subtotal, NOW)).toThrow(RangeError)
  })

  it('does not mutate source rows or accept a date-only claim as proof of ownership', () => {
    const source = { ...OWNED, claimed_at: '2026-10-10' }
    const saved = { ...source }
    expect(evaluateCoupons([source], 10000, NOW)[0].eligible).toBe(false)
    expect(source).toEqual(saved)
  })
})
