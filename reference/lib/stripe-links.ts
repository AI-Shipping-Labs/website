export type StripeTier = "basic" | "main" | "premium"

const paymentLinks: Record<StripeTier, { monthly: string; annual: string }> = {
  basic: {
    monthly: "https://buy.stripe.com/14A4gy0F1b610WdewRgbm00",
    annual: "https://buy.stripe.com/3cIcN44VhfmhbARagBgbm01",
  },
  main: {
    monthly: "https://buy.stripe.com/dRm8wOgDZca5bARbkFgbm02",
    annual: "https://buy.stripe.com/3cI3cu0F12zvcEV4Whgbm06",
  },
  premium: {
    monthly: "https://buy.stripe.com/dRm3cu4Vh7TPawN3Sdgbm04",
    annual: "https://buy.stripe.com/4gM9AS1J50rneN360lgbm05",
  },
}

export const CUSTOMER_PORTAL_URL = "https://billing.stripe.com/p/login/14A4gy0F1b610WdewRgbm00"

export function getPaymentLink(tier: StripeTier, annual: boolean): string {
  return annual ? paymentLinks[tier].annual : paymentLinks[tier].monthly
}
