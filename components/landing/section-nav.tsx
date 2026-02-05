"use client"

import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

const sections = [
  { id: "about", label: "Philosophy" },
  { id: "activities", label: "Activities" },
  { id: "tiers", label: "Membership" },
  { id: "testimonials", label: "Testimonials" },
  { id: "newsletter", label: "Newsletter" },
  { id: "faq", label: "FAQ" },
]

export function SectionNav() {
  const [activeSection, setActiveSection] = useState("")

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setActiveSection(entry.target.id)
          }
        })
      },
      {
        rootMargin: "-50% 0px -50% 0px",
        threshold: 0,
      }
    )

    sections.forEach(({ id }) => {
      const element = document.getElementById(id)
      if (element) observer.observe(element)
    })

    return () => observer.disconnect()
  }, [])

  const scrollToSection = (id: string) => {
    const element = document.getElementById(id)
    if (element) {
      element.scrollIntoView({ behavior: "smooth" })
    }
  }

  return (
    <nav className="fixed right-6 top-1/2 z-40 hidden -translate-y-1/2 xl:block">
      <ul className="flex flex-col gap-3">
        {sections.map(({ id, label }) => (
          <li key={id}>
            <button
              onClick={() => scrollToSection(id)}
              className={cn(
                "group flex items-center gap-3 text-sm transition-all",
                activeSection === id ? "text-accent" : "text-muted-foreground hover:text-foreground"
              )}
            >
              <span
                className={cn(
                  "h-2 w-2 rounded-full transition-all",
                  activeSection === id 
                    ? "bg-accent scale-125" 
                    : "bg-muted-foreground/40 group-hover:bg-foreground/60"
                )}
              />
              <span
                className={cn(
                  "opacity-0 transition-opacity group-hover:opacity-100",
                  activeSection === id && "opacity-100"
                )}
              >
                {label}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </nav>
  )
}
