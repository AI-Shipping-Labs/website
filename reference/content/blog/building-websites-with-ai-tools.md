---
title: "How I Rebuilt My Website in 10 Minutes With AI"
description: "I hadn't updated my personal website since 2012. Using AI tools like Lovable and GitHub Copilot, I rebuilt it from scratch in under 10 minutes. Here's exactly how I did it and what I learned."
date: "2025-12-05"
tags: ["ai-tools", "web-development", "tutorial", "productivity"]
author: "Alexey Grigorev"
---

I haven't properly updated [my personal website](https://alexeygrigorev.com/) since 2012.

Yesterday I tried a small experiment: I asked Lovable to generate a GitHub-style template for my homepage.

Surprisingly, the first iteration was already good enough.

I then exported the generated project to GitHub and asked GitHub Copilot to rewrite it in Jekyll, since Lovable is built with React, but GitHub Pages needs Jekyll.

After a few minor fixes, the site was ready, all in under ten minutes.

You can see my updated website here: [alexeygrigorev.com](https://alexeygrigorev.com/)

## How I Did It With AI

Here's the exact workflow I followed, using AI tools to handle different aspects of the development process:

### 1. Start with AI-Powered UI Generation

My initial prompt to Lovable was simple and specific:

```
I want to create my personal page that looks like a github profile: https://github.com/alexeygrigorev

userpic https://avatars.githubusercontent.com/u/875246?v=4 
name: Alexey Grigorev (alexeygrigorev)

instead of "Overview Repositories Projects Packages Stars" 
we can have "Overview Courses Projects CV"
```

Then I asked it to add my [README](https://github.com/alexeygrigorev/alexeygrigorev/blob/master/README.md) and support dark/light mode:

```
Add dark/light mode for overview use the alexeygrigorev/README.md

<CONTENT OF README.md>
```

Lovable produced a [working version](https://neo-github-me.lovable.app/) on the first try. The key was providing clear, specific instructions with visual references and actual content. This saved hours of manual coding.

<figure>
  <img 
    src="/images/blog/ai-generated-website-prototype.png" 
    alt="Lovable AI-generated GitHub-style personal website with dark mode interface showing overview, courses, projects, and CV sections"
    loading="lazy"
    width="1200"
    height="800"
  />
  <figcaption>AI-generated website prototype created with Lovable in under 3 minutes</figcaption>
</figure>

### 2. Framework Translation with GitHub Copilot

From there, the workflow was straightforward. I exported the project to GitHub, opened an issue, and assigned it to Copilot with this instruction:

```
Redo it with Jekyll

This page can be served statically. I want to use Jekyll for that.
```

Copilot handled the entire framework conversion from React to Jekyll, understanding that GitHub Pages needs Jekyll for static hosting. This type of translation would typically take hours manually, but the AI handled it efficiently.

### 3. Refactoring for Maintainability

After cloning the repo to my local computer, I asked Copilot in VS Code to make a few specific improvements:

```
Make each tab a separate page, instead of showing tabs dynamically via JavaScript

Move the data from HTML to YAML files in _data/ to make updates easier
```

These refactoring steps separated content from structure, making future updates much easier. I then refreshed my CV, let Copilot polish the layout, replaced the old site files, and added the courses and resources sections.

<figure>
  <img 
    src="/images/blog/ai-generated-website-final.png" 
    alt="Final deployed personal website built with AI tools - Jekyll-based homepage on GitHub Pages featuring profile overview, course catalog, and professional CV"
    loading="lazy"
    width="1200"
    height="800"
  />
  <figcaption>Final deployed website on GitHub Pages, converted from React to Jekyll using GitHub Copilot</figcaption>
</figure>

## Tools I Used

Here are the specific tools that made this possible:

### Lovable
Handles layout and UI generation. You can use 5 free daily credits to experiment. It excels at:
- Creating visual designs from natural language prompts
- Handling responsive design automatically
- Supporting modern frameworks like React
- Providing real-time preview and iteration

### GitHub Copilot
Handles framework translation and refactoring. It's included in GitHub Pro plans and can:
- Translate between frameworks and languages (React to Jekyll in my case)
- Suggest best practices and optimizations
- Handle repetitive refactoring tasks
- Integrate directly into VS Code and GitHub

### GitHub Pages
Handles hosting. It's free to use and provides:
- Static site hosting
- Built-in CI/CD pipelines
- Custom domain support
- Automatic HTTPS

## Key Takeaways

**It's amazing how easy this has become**: Using AI tools that take natural-language instructions, I rebuilt a website I hadn't touched in over a decade in under 10 minutes.

**Specific prompts with examples work best**: Referencing concrete examples (my GitHub profile) and providing specific details (avatar URL, section names) gave Lovable exactly what it needed, not vague descriptions.

**You don't need deep technical knowledge**: You don't need to understand Jekyll or be a React expert. Copilot handled translating React components to Jekyll templates and Liquid syntax automatically.

**Chain specialized tools together**: Lovable for UI, Copilot for framework translation and refactoring, GitHub Pages for hosting. Each tool did what it does best.

**Content separation pays off immediately**: Moving data to YAML files in `_data/` means I can update my courses and projects without touching any code going forward.

**The first result can surprise you**: Lovable's first iteration was already good enough to use. Don't over-engineer the initial prompt: see what you get and iterate from there.

The technology has evolved to the point where the hardest part is deciding what content to include, not how to build it.

My 2012 website sat untouched for 13 years because updating it seemed like a project. With these AI tools, it took less time than making coffee.

## The Complete Workflow & How You Can Do This Too

Here's exactly how my 10-minute rebuild broke down, with actionable steps you can follow:

**1. Initial Generation with Lovable** (~3 minutes)
- Pick a visual reference (I used my GitHub profile)
- Write a specific prompt with concrete details (avatar URL, section names, etc.)
- Include your actual content (README, bio, etc.)
- Add any features you want (like dark/light mode)
- Generate and review the first version

**2. Framework Translation** (~2 minutes)
- Export the project to GitHub
- Open an issue and assign it to Copilot (if you have GitHub Pro)
- Specify your target framework (Jekyll for GitHub Pages in my case)
- Let Copilot handle the conversion

**3. Local Refactoring** (~3 minutes)
- Clone the repo locally
- Use Copilot in VS Code for improvements (separate pages, move data to YAML, etc.)
- Make any minor fixes needed

**4. Content Updates and Deployment** (~2 minutes)
- Add or refresh your actual content
- Push to GitHub Pages (free hosting)
- Test the live site
