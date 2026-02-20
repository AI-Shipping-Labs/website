import { Header } from "@/components/landing/header"
import { Hero } from "@/components/landing/hero"
import { About } from "@/components/landing/about"
import { Pricing } from "@/components/landing/pricing"
import { Testimonials } from "@/components/landing/testimonials"
import { BlogSection } from "@/components/landing/blog-section"
import { ProjectsSection } from "@/components/landing/projects-section"
import { CollectionSection } from "@/components/landing/collection-section"
import { ResourcesSection } from "@/components/landing/resources-section"
import { Newsletter } from "@/components/landing/newsletter"
import { FAQ } from "@/components/landing/faq"
import { Footer } from "@/components/landing/footer"
import { SectionNav } from "@/components/landing/section-nav"

export default function Home() {
  return (
    <main className="min-h-screen">
      <Header />
      <SectionNav />
      <Hero />
      <About />
      <Pricing />
      <Testimonials />
      <ResourcesSection />
      <BlogSection />
      <ProjectsSection />
      <CollectionSection />
      <Newsletter />
      <FAQ />
      <Footer />
    </main>
  )
}
