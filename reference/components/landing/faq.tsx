"use client"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"

const faqs = [
  {
    question: "Who is this community for?",
    answer: "Action-oriented builders interested in AI engineering and AI tools who want to turn ideas into real projects. Whether you're learning Python or working as an ML engineer, if you have project ideas but need structure, focus, and accountability, this community is for you. We attract motivated learners who prefer learning by doing and builders who contribute back to the ecosystem.",
  },
  {
    question: "What makes this different from other tech communities?",
    answer: "We focus on helping you ship practical AI products, not just consume content. You get clear frameworks, direction, and gentle external pressure to make consistent progress on your projects. The community concentrates highly engaged builders in a focused environment centered on productivity, structured execution, and hands-on project work.",
  },
  {
    question: "I have a main job. Can I still participate?",
    answer: "Yes. The community is designed to help you make consistent progress on side projects even with limited time. You get the structure and accountability to stay focused and ship incrementally through projects, hackathons, and collaborative activities.",
  },
  {
    question: "What if I just want the content without community?",
    answer: "The Basic tier is designed exactly for this. You get access to exclusive content, tutorials, research, and curated materials without any expectation of community participation. Perfect for self-directed builders who learn at their own pace.",
  },
  {
    question: "What's included in the Main tier?",
    answer: "Main tier gives you the structure, accountability, and peer support to ship your AI projects consistently. Includes everything in Basic, plus closed community access, collaborative problem-solving, interactive group coding sessions, guided projects, hackathons, career discussions, and the ability to propose and vote on topics.",
  },
  {
    question: "What's included in the Premium tier?",
    answer: "Premium tier accelerates your growth with structured learning paths through mini-courses and personalized career guidance. Includes everything in Main, plus access to all mini-courses on specialized topics, the ability to vote on course topics, and professional profile teardowns (resume, LinkedIn, GitHub).",
  },
  {
    question: "How do I get started?",
    answer: "Pick the tier that fits your needs, click the button to check out securely via Stripe, and you'll receive access details by email within 24 hours. You can start with any tier and upgrade or downgrade at any time.",
  },
  {
    question: "How does billing work?",
    answer: "All payments are processed securely through Stripe. You can choose monthly or annual billing (annual saves ~17%). Stripe handles tax calculation automatically based on your location. You'll receive invoices and receipts by email after each payment.",
  },
  {
    question: "Can I cancel or change my subscription?",
    answer: "Yes, you're in full control. You can cancel, upgrade, downgrade, or update your payment method at any time through the Stripe Customer Portal. If you cancel, you'll retain access until the end of your current billing period.",
  },
]

export function FAQ() {
  return (
    <section id="faq" className="border-t border-border bg-background py-24 lg:py-32">
      <div className="mx-auto max-w-3xl px-6 lg:px-8">
        <div className="text-center">
          <p className="text-sm font-medium uppercase tracking-widest text-accent">FAQ</p>
          <h2 className="mt-4 text-balance text-3xl font-semibold tracking-tight sm:text-4xl">
            Common questions
          </h2>
        </div>

        <Accordion type="single" collapsible className="mt-12">
          {faqs.map((faq, index) => (
            <AccordionItem key={index} value={`item-${index}`} className="border-border">
              <AccordionTrigger className="text-left text-foreground hover:text-accent hover:no-underline">
                {faq.question}
              </AccordionTrigger>
              <AccordionContent className="text-muted-foreground">
                {faq.answer}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </section>
  )
}
