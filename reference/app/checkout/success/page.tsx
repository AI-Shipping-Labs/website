import Link from "next/link"
import { CheckCircle, ArrowLeft } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { CUSTOMER_PORTAL_URL } from "@/lib/stripe-links"

export const metadata = {
  title: "Welcome aboard! | AI Shipping Labs",
  description: "Your subscription is confirmed. Welcome to AI Shipping Labs.",
}

export default function CheckoutSuccessPage() {
  return (
    <main className="min-h-screen">
      <Header />

      <section className="px-6 pt-32 pb-16 lg:px-8 lg:pt-40 lg:pb-24">
        <div className="mx-auto max-w-2xl text-center">
          <div className="flex justify-center">
            <CheckCircle className="h-16 w-16 text-accent" />
          </div>

          <h1 className="mt-6 text-3xl font-bold tracking-tight sm:text-4xl">
            Welcome aboard!
          </h1>
          <p className="mt-4 text-lg text-muted-foreground">
            Your subscription is confirmed. You're now part of AI Shipping Labs.
          </p>

          <div className="mt-12 rounded-2xl border border-border bg-card p-8 text-left">
            <h2 className="text-lg font-semibold">What happens next</h2>
            <ol className="mt-4 space-y-4 text-muted-foreground">
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-medium text-accent-foreground">
                  1
                </span>
                <span>
                  <strong className="text-foreground">Check your email</strong> — you'll receive a receipt and subscription confirmation from Stripe.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-medium text-accent-foreground">
                  2
                </span>
                <span>
                  <strong className="text-foreground">Receive your invite</strong> — we'll send you access details to the community within 24 hours.
                </span>
              </li>
              <li className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-medium text-accent-foreground">
                  3
                </span>
                <span>
                  <strong className="text-foreground">Introduce yourself</strong> — once you're in, say hello and tell us what you're building.
                </span>
              </li>
            </ol>
          </div>

          <div className="mt-8 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Button asChild className="bg-accent text-accent-foreground hover:bg-accent/90">
              <Link href="/">
                <ArrowLeft className="mr-2 h-4 w-4" />
                Back to Home
              </Link>
            </Button>
            <Button asChild variant="outline">
              <a href={CUSTOMER_PORTAL_URL} target="_blank" rel="noopener noreferrer">
                Manage Subscription
              </a>
            </Button>
          </div>
        </div>
      </section>

      <Footer />
    </main>
  )
}
