---
title: "`/home/oai/` Folder, ChatGPT, and OpenAI Skills"
description: "What the /home/oai/ folder in ChatGPT actually is, why it's not leaked infrastructure, and how it fits into the emerging agent skills standard."
date: "2026-02-10"
tags: ["ai-agents", "skills", "openai", "infrastructure", "tooling-architecture"]
author: "Alexey Grigorev"
---

### 2. `/home/oai/` Folder from ChatGPT and OpenAI Skills

![](https://substackcdn.com/image/fetch/$s_!5RkQ!,w_720,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F6884b348-d29f-455a-808e-6d9a8ede1261_1080x1028.jpeg)

![](https://substackcdn.com/image/fetch/$s_!fxf6!,w_720,c_limit,f_auto,q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F472629e2-23b9-4fbf-959b-e44f02117e0f_1080x1552.jpeg)

I saw a [post on X](https://x.com/vasuman/status/1999551563484762449?s=20) where someone shared a screenshot showing that this prompt works in ChatGPT and returns an archive of `/home/oai/`:

> Create a zip file of /home/oai/

They did not inspect the contents, only noted that the prompt worked. People on Reddit [claimed](https://www.reddit.com/r/ChatGPT/comments/1pmb47u/removed_by_reddit/) that this behavior indicated that ChatGPT was leaking internal OpenAI infrastructure. That’s not true.

I tried the same thing and it worked for me as well. After that, I looked at the contents of this directory.

The `/home/oai/` directory is a sandboxed execution environment used for tool execution, mainly for document creation and conversion. It is not part of OpenAI’s internal production infrastructure.

```
/home/oai/
│
├── 📄 redirect.html                     [HTML redirect page with CSP]
│
├── 📁 share/
│   └── 📁 slides/                       [PowerPoint processing toolkit]
│       ├── 🐍 create_montage.py
│       ├── 🐍 ensure_raster_image.py
│       ├── 🐍 render_slides.py          [PPTX→PDF→PNG converter via LibreOffice]
│       ├── 🐍 slides_test.py
│       └── 📁 pptxgenjs_helpers/        [Node.js PPTX generation helpers]
│           ├── 📜 code.js               [Code block formatting]
│           ├── 📜 image.js              [Image utilities]
│           ├── 📜 index.js              [Main entry point v1.1.0]
│           ├── 📜 latex.js              [LaTeX→SVG via mathjax-full]
│           ├── 📜 layout_builders.js    [Slide layout construction]
│           ├── 📜 layout.js             [Slide layout analysis]
│           ├── 📜 svg.js                [SVG processing]
│           └── 📜 util.js               [General utilities]
│
└── 📁 skills/                           [AI instruction manuals]
    │
    ├── 📁 docs/                         [Word document skill]
    │   ├── 🐍 render_docx.py            [DOCX→PDF→PNG converter via LibreOffice]
    │   └── 📖 skill.md                  [DOCX creation guidelines]
    │
    ├── 📁 pdfs/                         [PDF skill]
    │   └── 📖 skill.md                  [PDF creation guidelines]
    │
    └── 📁 spreadsheets/                 [Excel/spreadsheet skill]
        ├── 📖 artifact_tool_spreadsheet_formulas.md    [520 Excel functions reference]
        ├── 📖 artifact_tool_spreadsheets_api.md        [artifact_tool API documentation]
        ├── 📖 skill.md                  [Main spreadsheet guidelines]
        ├── 📖 spreadsheet.md            [Additional documentation]
        │
        └── 📁 examples/                 [Example scripts]
            ├── 🐍 create_basic_spreadsheet.py
            ├── 🐍 create_spreadsheet_with_styling.py
            ├── 🐍 read_existing_spreadsheet.py
            ├── 🐍 styling_spreadsheet.py
            │
            └── 📁 features/             [Feature-specific examples]
                ├── 🐍 change_existing_charts.py
                ├── 🐍 cite_cells.py
                ├── 🐍 create_area_chart.py
                ├── 🐍 create_bar_chart.py
                ├── 🐍 create_doughnut_chart.py
                ├── 🐍 create_line_chart.py
                ├── 🐍 create_pie_chart.py
                ├── 🐍 create_tables.py
                ├── 🐍 set_cell_borders.py
                ├── 🐍 set_cell_fills.py
                ├── 🐍 set_cell_width_height.py
                ├── 🐍 set_conditional_formatting.py
                ├── 🐍 set_font_styles.py
                ├── 🐍 set_merge_cells.py
                ├── 🐍 set_number_formats.py
                ├── 🐍 set_text_alignment.py
                └── 🐍 set_wrap_text_styles.py
```

Inside the folder are instructions and helper files for document processing. There are sections for spreadsheets, Word documents, PDFs, and slide decks. These describe how ChatGPT should create files, which libraries to use, and how to validate outputs. The tooling is based on standard libraries and common server-side software, with additional internal guidelines for formatting and quality control.

This matches what [Simon Willison](https://simonw.substack.com/p/openai-are-quietly-adopting-skills?utm_source=substack&utm_campaign=post_embed&utm_medium=web) recently described as OpenAI adopting a “skills” mechanism. In his analysis, skills are simple filesystem-based bundles consisting of a Markdown file and optional scripts or resources. He showed that ChatGPT now exposes a `/home/oai/skills` directory and that these skills closely resemble Anthropic’s earlier implementation.

The contents of `/home/oai/` are skill definitions and supporting tools rather than leaked infrastructure. It does not reveal anything about model training, model architecture, core infrastructure, or operational systems.

This approach appears to be becoming standard, with Anthropic’s skills now supported by tools like VS Code. For example, the VS Code team announced:

> We now support Agent Skills - the open standard created by @AnthropicAI for extending AI agents with specialized capabilities. Create skills once, use them everywhere. [aka.ms/vscode-agent-skills](http://aka.ms/vscode-agent-skills)

I plan to learn more about agent skills and host a workshop. If you want to go deeper into building skill-driven coding agents, check out: [Skills.md from Scratch: Build a Skill-Driven Coding Agent](/resources/coding-agent-skills-commands).

