import { Quote } from "lucide-react"

const testimonials = [
  {
    quote: "This course helped me understand how to implement a RAG system in Python. From basic system-design of a RAG, to evaluating responses and implementing guardrails, the course gave me a great overview of the necessary skills for implementing and managing my own agent.",
    name: "Rolando",
    role: "AI Data Scientist",
    company: "AeroMexico",
  },
  {
    quote: "I highly recommend the AI Engineering Buildcamp. I learned a tremendous amount. The material is abundant, very well organized, and progresses in a logical and progressive manner. This made complex topics much easier to follow and digest. The instructor Alexey Grigorev is clearly very knowledgeable in the field, and also super helpful and responsive to questions.",
    name: "John",
    role: "AI Tutor",
    company: "Meta",
  },
  {
    quote: "Excellent, comprehensive, and modern course that elevated my knowledge of generative AI from RAG applications to well-evaluated, fully functioning agentic systems. Alexey Grigorev incorporated essential software engineering practices, especially unit testing and evaluation, teaching us how to systematically improve our agents.",
    name: "Yan",
    role: "Senior Data Scientist",
    company: "Virtualitics",
  },
  {
    quote: "I really enjoyed this course! It made the process of building AI agents both accessible and exciting. The progression from RAG to agents, multi-agent systems, monitoring, and guardrails was clear and practical. I'm walking away inspired and full of new ideas to build on.",
    name: "Scott",
    role: "Principal Data Scientist, Applied AI",
    company: "interos.ai",
  },
  {
    quote: "The course provides an excellent introduction to the core tooling needed to develop an agentic tool. Worth the effort especially given the comprehensiveness of the options and solutions available in the course.",
    name: "Naveen",
    role: "Software Engineer",
    company: "",
  },
  {
    quote: "Excellent course, it gets you practicing the concepts you need to know to work on agentic AI. The instructor is accessible, clear, and flexible.",
    name: "Nelson",
    role: "Practitioner",
    company: "",
  },
]

export function Testimonials() {
  return (
    <section id="testimonials" className="border-t border-border bg-card px-6 py-24 lg:px-8 lg:py-32">
      <div className="mx-auto max-w-7xl">
        <div className="text-center">
          <p className="text-sm font-medium uppercase tracking-wider text-accent">What learners say</p>
          <h2 className="mt-4 text-balance text-2xl font-semibold tracking-tight sm:text-3xl">
            From the students of our AI Engineering course
          </h2>
          <p className="mt-4 text-muted-foreground max-w-2xl mx-auto">
            AI Shipping Labs community is new, but here's what practitioners say about the courses that inspired it.
          </p>
        </div>

        <div className="mt-16 columns-1 gap-6 sm:columns-2 lg:columns-3">
          {testimonials.map((testimonial, index) => (
            <div
              key={index}
              className="mb-6 break-inside-avoid rounded-xl border border-border bg-background p-6"
            >
              <Quote className="h-6 w-6 text-accent/50" />
              <blockquote className="mt-4 text-sm leading-relaxed text-muted-foreground">
                "{testimonial.quote}"
              </blockquote>
              <div className="mt-6 flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary text-sm font-medium">
                  {testimonial.name.charAt(0)}
                </div>
                <div>
                  <p className="font-medium text-sm">{testimonial.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {testimonial.role}
                    {testimonial.company && ` · ${testimonial.company}`}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
