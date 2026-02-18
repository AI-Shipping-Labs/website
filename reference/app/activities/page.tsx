import { Header } from "@/components/landing/header"
import { Footer } from "@/components/landing/footer"
import { Activities } from "@/components/landing/activities"

export const metadata = {
  title: "Activities and Access by Tier | AI Shipping Labs",
  description: "Explore all activities and access levels by tier. Each tier gives you more structure, accountability, and support to ship your AI projects.",
}

export default function ActivitiesPage() {
  return (
    <main className="min-h-screen">
      <Header />
      <Activities />
      <Footer />
    </main>
  )
}
