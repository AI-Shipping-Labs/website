---
title: "How I Rebuilt My Website in 10 Minutes With AI"
description: "I used AI to rebuild my website in 10 minutes. Here's how I did it."
date: "2025-02-05"
tags: ["ai-engineering", "personal-website"]
---

I hadn’t properly updated my [personal website](https://alexeygrigorev.com/) since 2012.

Yesterday I tried a small experiment: I asked Lovable to generate a GitHub-style template for my homepage.

Surprisingly, the first iteration was already good enough.

I then exported the generated project to GitHub and asked GitHub Copilot to rewrite it in Jekyll, since Lovable is built with React but GitHub Pages needs Jekyll.

After a few minor fixes, the site was ready — all in under ten minutes.

My updated website: [alexeygrigorev.com](https://alexeygrigorev.com/)

## How I Did It With AI

My initial prompt to Lovable was simple:

> I want to create my personal page that looks like a GitHub profile: https://github.com/alexeygrigorev  
> userpic https://avatars.githubusercontent.com/u/875246?v=4 name: Alexey Grigorev (alexeygrigorev)  
> instead of “Overview Repositories Projects Packages Stars” we can have “Overview Courses Projects CV”

Then I asked it to add my README and support dark/light mode:

> Add dark/light mode for overview, use the alexeygrigorev/README.md  
> (then I pasted the content of my GitHub profile README)

Lovable produced this version: https://neo-github-me.lovable.app/

## From Lovable to Jekyll

From there, the workflow was straightforward:

1. I exported the project to GitHub, opened an issue, and assigned it to Copilot with the instruction:
   > Redo it with Jekyll  
   > This page can be served statically. I want to use Jekyll for that.
2. After cloning the repo to my local computer, I asked Copilot in VS Code to make a few other edits:
   - Make each tab a separate page, instead of showing tabs dynamically via JavaScript  
   - Move the data from HTML to YAML files in `_data/` to make updates easier

I refreshed my CV, let Copilot polish the layout, replaced the old site files, and added the courses and resources sections.

## Tools I Used

- **Lovable** handles layout and UI: you get 5 free daily credits.
- **GitHub Copilot** handles framework translation: it’s included in my GitHub Pro plan.
- **GitHub Pages** handles hosting: it’s free to use.

It’s amazing how easy it has become to build a clean, functional homepage using AI tools that take natural-language instructions.