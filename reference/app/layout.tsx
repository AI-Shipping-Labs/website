import React from "react"
import type { Metadata } from 'next'
import { Inter, JetBrains_Mono, Dancing_Script } from 'next/font/google'

import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
})

const dancingScript = Dancing_Script({
  subsets: ['latin'],
  variable: '--font-script',
})

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://ai-shipping-labs.com'

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: 'AI Shipping Labs | A Technical Community',
    template: '%s | AI Shipping Labs',
  },
  description: 'An invite-only community for action-oriented builders who want to turn AI ideas into real projects.',
  keywords: ['AI community', 'AI engineering', 'machine learning', 'data engineering', 'AI tools', 'technical community', 'Alexey Grigorev'],
  authors: [{ name: 'Alexey Grigorev' }],
  creator: 'Alexey Grigorev',
  openGraph: {
    type: 'website',
    locale: 'en_US',
    url: siteUrl,
    siteName: 'AI Shipping Labs',
    title: 'AI Shipping Labs | A Technical Community',
    description: 'An invite-only community for action-oriented builders who want to turn AI ideas into real projects.',
    images: [
      {
        url: '/og-image.png', // You'll need to create this image
        width: 1200,
        height: 630,
        alt: 'AI Shipping Labs - Turn AI ideas into real projects',
      },
    ],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'AI Shipping Labs | A Technical Community',
    description: 'An invite-only community for action-oriented builders who want to turn AI ideas into real projects.',
    images: ['/og-image.png'], // You'll need to create this image
    creator: '@alexeygrigorev', // Update with actual Twitter handle if available
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
  icons: {
    icon: '/favicon.ico',
    apple: '/apple-touch-icon.png',
  },
  verification: {
    // Add verification codes when available
    // google: 'your-google-verification-code',
    // yandex: 'your-yandex-verification-code',
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  const structuredData = {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: 'AI Shipping Labs',
    description: 'An invite-only community for action-oriented builders who want to turn AI ideas into real projects.',
    url: siteUrl,
    founder: {
      '@type': 'Person',
      name: 'Alexey Grigorev',
    },
    sameAs: [
      // Add social media links when available
      // 'https://twitter.com/alexeygrigorev',
      // 'https://linkedin.com/in/agrigorev',
    ],
  }

  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} ${dancingScript.variable}`}>
      <head>
        {/* Google tag (gtag.js) */}
        <script async src="https://www.googletagmanager.com/gtag/js?id=G-HXSHF376NY"></script>
        <script
          dangerouslySetInnerHTML={{
            __html: `
              window.dataLayer = window.dataLayer || [];
              function gtag(){dataLayer.push(arguments);}
              gtag('js', new Date());
              gtag('config', 'G-HXSHF376NY');
            `,
          }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
        />
      </head>
      <body className="font-sans antialiased bg-background text-foreground">{children}</body>
    </html>
  )
}
