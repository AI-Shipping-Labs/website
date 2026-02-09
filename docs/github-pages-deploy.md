# Deploying to GitHub Pages

This Next.js app is configured for **static export** and can be deployed to GitHub Pages.

## What’s configured

- **`output: "export"`** in `next.config.mjs` — `npm run build` writes static files to the `out/` directory.
- **GitHub Actions workflow** (`.github/workflows/deploy-pages.yml`) — on push to `main`, it builds the site and deploys the `out/` folder to GitHub Pages.

## One-time setup

1. In your repo, go to **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.

After that, every push to `main` will build and deploy the site.

## Project vs user/org site

- **User or org site** (e.g. `https://username.github.io`): no code changes. The workflow deploys to the root.
- **Project site** (e.g. `https://username.github.io/ai-community-landing-page`): uncomment in `next.config.mjs`:
  ```js
  basePath: "/ai-community-landing-page",
  assetPrefix: "/ai-community-landing-page/",
  ```
  Use your repo name as `basePath` and `assetPrefix` so assets and links work correctly.

## Local check

```bash
npm run build
npx serve out
```

Then open the URL shown (e.g. `http://localhost:3000`) to verify the static site.
