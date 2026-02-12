# AI Shipping Labs Landing Page

A Next.js static website for the AI Shipping Labs community - an invite-oriented community for AI, data, and engineering practitioners.

## Overview

This is a static Next.js application that serves as a landing page and content hub for the AI Shipping Labs community. The site features blog posts, projects, resources, tutorials, and community information.

## Tech Stack

- **Framework**: Next.js 16.0.7 (with static export)
- **Language**: TypeScript 5.7.3
- **Styling**: Tailwind CSS 3.4.17
- **UI Components**: Radix UI primitives
- **Content**: Markdown files processed with `gray-matter` and `remark`
- **Fonts**: Inter, JetBrains Mono, Dancing Script (via Next.js Google Fonts)

## Project Structure

```
├── app/                    # Next.js app directory
│   ├── about/             # About page
│   ├── blog/              # Blog listing and individual posts
│   ├── collection/        # Collection page
│   ├── projects/          # Projects listing and individual projects
│   ├── resources/         # Resources listing and individual resources
│   ├── topics/            # Topics page
│   ├── tutorials/         # Tutorials listing and individual tutorials
│   ├── layout.tsx         # Root layout with metadata
│   ├── page.tsx           # Home page
│   └── globals.css        # Global styles
├── components/
│   ├── landing/           # Landing page components
│   │   ├── header.tsx
│   │   ├── hero.tsx
│   │   ├── about.tsx
│   │   ├── activities.tsx
│   │   ├── pricing.tsx
│   │   ├── testimonials.tsx
│   │   ├── blog-section.tsx
│   │   ├── tutorials-section.tsx
│   │   ├── projects-section.tsx
│   │   ├── collection-section.tsx
│   │   ├── resources-section.tsx
│   │   ├── newsletter.tsx
│   │   ├── faq.tsx
│   │   ├── footer.tsx
│   │   └── section-nav.tsx
│   └── ui/                # Reusable UI components (Radix UI based)
├── content/               # Markdown content files
│   ├── blog/             # Blog posts
│   ├── projects/         # Project descriptions
│   └── resources/        # Resource articles
├── lib/                   # Utility functions
│   ├── blog.ts           # Blog post processing
│   ├── projects.ts       # Project processing
│   ├── resources.ts      # Resource processing
│   ├── tutorials.ts      # Tutorial processing
│   ├── collection.ts     # Collection processing
│   └── utils.ts          # General utilities
├── hooks/                 # React hooks
├── public/                # Static assets (images, etc.)
├── styles/                # Additional stylesheets
└── .github/
    └── workflows/
        └── deploy-pages.yml  # GitHub Pages deployment workflow
```

## Features

### Pages

- **Home**: Landing page with hero, about, activities, pricing, testimonials, blog section, tutorials, projects, collection, resources, newsletter, and FAQ
- **About**: About page
- **Blog**: Blog post listing and individual blog post pages
- **Projects**: Project showcase listing and individual project pages
- **Resources**: Resource articles listing and individual resource pages
- **Tutorials**: Tutorial listing and individual tutorial pages
- **Collection**: Collection page
- **Topics**: Topics page

### Content Management

- Content is stored as Markdown files in the `content/` directory
- Frontmatter metadata (title, description, date, tags, etc.) is extracted using `gray-matter`
- Markdown is converted to HTML using `remark` and `remark-html`
- Reading time is automatically calculated
- Content is sorted by date (newest first)

## Getting Started

### Prerequisites

- Node.js 20 or higher
- npm, pnpm, or yarn

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd ai-community-landing-page
```

2. Install dependencies:
```bash
npm install
# or
pnpm install
```

### Development

Run the development server:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Build

Build the static site:

```bash
npm run build
```

This generates a static export in the `out/` directory.

### Preview Production Build

To preview the production build locally:

```bash
npm run build
npx serve out
```

## Deployment

The site is configured for static export and deployed to GitHub Pages via GitHub Actions.

### GitHub Pages Deployment

1. The repository includes a GitHub Actions workflow (`.github/workflows/deploy-pages.yml`)
2. On push to `main`, the workflow:
   - Installs dependencies
   - Builds the static site
   - Deploys the `out/` directory to GitHub Pages

3. One-time setup:
   - Go to repository Settings → Pages
   - Set Source to "GitHub Actions"

### Configuration

For project sites (deployed to `https://username.github.io/repo-name/`), uncomment the `basePath` and `assetPrefix` in `next.config.mjs`:

```js
basePath: "/ai-community-landing-page",
assetPrefix: "/ai-community-landing-page/",
```

## Content Structure

### Blog Posts

Located in `content/blog/`. Each file should have frontmatter:

```markdown
---
title: "Post Title"
description: "Post description"
date: "2024-01-01"
tags: ["tag1", "tag2"]
---
```

### Projects

Located in `content/projects/`. Frontmatter can include:

```markdown
---
title: "Project Name"
description: "Project description"
date: "2024-01-01"
author: "Author Name"
tags: ["tag1", "tag2"]
difficulty: "beginner" | "intermediate" | "advanced"
estimatedTime: "2 hours"
---
```

### Resources

Located in `content/resources/`. Similar frontmatter structure as blog posts.

## Scripts

- `npm run dev` - Start development server
- `npm run build` - Build static site to `out/` directory
- `npm run start` - Start production server (for testing)
- `npm run lint` - Run ESLint

## Configuration Files

- `next.config.mjs` - Next.js configuration (static export enabled)
- `tailwind.config.ts` - Tailwind CSS configuration
- `tsconfig.json` - TypeScript configuration
- `postcss.config.mjs` - PostCSS configuration
- `components.json` - shadcn/ui components configuration

## Notes

- The `out/` directory contains the static build output and should not be committed to git (it's generated automatically)
- Images should be placed in `public/images/` and referenced from there
- The site uses static export (`output: "export"` in `next.config.mjs`), so no server-side features are available
