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

export const metadata: Metadata = {
  title: 'AI Engineering Lab | A Technical Community by Alexey Grigorev',
  description: 'An invite-oriented community for AI, data, and engineering practitioners. Signal over noise. Judgment over content.',
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrainsMono.variable} ${dancingScript.variable}`}>
      <body className="font-sans antialiased bg-background text-foreground">{children}</body>
    </html>
  )
}
